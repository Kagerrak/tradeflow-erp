from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from tradeflow_api.auth import (
    AuthorizedUser,
    require_quotation_approver,
    require_quotation_converter,
    require_quotation_writer,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    companies,
    customer_accounts,
    customer_address_versions,
    document_series,
    document_series_number_audit,
    price_list_lines,
    price_list_versions,
    price_lists,
    quotation_approval_exceptions,
    quotation_approvals,
    quotation_conversion_events,
    quotation_line_revisions,
    quotation_revisions,
    quotations,
    sales_order_line_revisions,
    sales_order_revisions,
    sales_orders,
    skus,
    tax_code_versions,
    tax_codes,
    unit_conversions,
)
from tradeflow_api.money import allocate_largest_remainder, currency_quantum

router = APIRouter(prefix="/v1/sales/quotations", tags=["quotations"])
SIX_PLACES = Decimal("0.000001")
PaymentTimingPolicy = Literal["prepaid", "cash_on_delivery", "on_account"]
PriceInclusionMode = Literal["inclusive", "exclusive"]
QuotationStatus = Literal["draft", "approved", "converted", "expired"]


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuotationLineInput(CommandModel):
    line_id: UUID
    sku_id: UUID
    expected_price_list_line_id: UUID
    expected_unit_conversion_id: UUID | None
    expected_unit_conversion_version: int | None = Field(default=None, ge=1)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")
    manual_override_unit_price: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=6
    )
    price_override_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def override_requires_reason(self) -> QuotationLineInput:
        if self.manual_override_unit_price is not None and self.price_override_reason is None:
            raise ValueError("A manual Price Override requires a reason.")
        if self.manual_override_unit_price is None and self.price_override_reason is not None:
            raise ValueError("A Price Override reason requires an override price.")
        return self


class QuotationDraftFields(CommandModel):
    branch_id: UUID
    customer_id: UUID
    expected_customer_version: int = Field(ge=1)
    expected_price_list_version_id: UUID
    expected_pricing_date: date
    delivery_address_version_id: UUID
    expiry_date: date
    payment_timing_policy: PaymentTimingPolicy | None = None
    payment_timing_override_reason: str | None = Field(default=None, min_length=1, max_length=500)
    order_discount_amount: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=18, decimal_places=6
    )
    lines: list[QuotationLineInput] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_line_ids(self) -> QuotationDraftFields:
        line_ids = [line.line_id for line in self.lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("Quotation Line identities must be unique.")
        return self

    @model_validator(mode="after")
    def expiry_after_pricing(self) -> QuotationDraftFields:
        if self.expiry_date < self.expected_pricing_date:
            raise ValueError("expiry_date must not precede pricing_date.")
        return self


class CreateQuotationCommand(QuotationDraftFields):
    pass


class UpdateQuotationCommand(QuotationDraftFields):
    pass


class QuotationLineResponse(BaseModel):
    line_id: UUID
    line_position: int
    sku_id: UUID
    sku_code: str
    sku_name: str
    entered_quantity: Decimal
    entered_unit: str
    quantity_base: Decimal
    conversion_snapshot: dict[str, str]
    price_list_line_id: UUID
    price_list_code: str
    price_list_version_id: UUID
    price_list_version: int
    price_source: Literal["customer", "branch"]
    list_unit_price: Decimal
    floor_unit_price: Decimal | None
    manual_override_unit_price: Decimal | None
    price_override_reason: str | None
    effective_unit_price: Decimal
    allocated_discount: Decimal
    tax_snapshot: dict[str, str]
    calculation_snapshot: dict[str, str]
    taxable_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal
    below_floor: bool


class QuotationResponse(BaseModel):
    quotation_id: UUID
    number: str
    status: QuotationStatus
    version: int
    branch_id: UUID
    customer_id: UUID
    customer_version: int
    delivery_address_version_id: UUID
    delivery_address_snapshot: dict[str, object]
    currency: str
    price_inclusion_mode: PriceInclusionMode
    price_list_version_id: UUID
    price_list_code: str
    price_list_version: int
    pricing_date: date
    expiry_date: date
    payment_timing_default: PaymentTimingPolicy
    payment_timing_policy: PaymentTimingPolicy
    payment_timing_override_reason: str | None
    payment_timing_overridden_by: str | None
    order_discount_amount: Decimal
    subtotal: Decimal
    discount_total: Decimal
    taxable_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    calculation_contract_version: int
    converted_sales_order_id: UUID | None
    lines: list[QuotationLineResponse]


class QuotationSearchItem(BaseModel):
    quotation_id: UUID
    number: str
    status: QuotationStatus
    version: int
    branch_id: UUID
    customer_id: UUID
    customer_name: str
    currency: str
    grand_total: Decimal
    expiry_date: date


class QuotationSearchResponse(BaseModel):
    items: list[QuotationSearchItem]
    total: int


class ApproveQuotationCommand(CommandModel):
    exception_reason: str | None = Field(default=None, min_length=1, max_length=500)


class QuotationApprovalExceptionResponse(BaseModel):
    exception_type: Literal["discount", "below_floor"]
    amount: Decimal
    percentage: Decimal | None


class QuotationApprovalResponse(BaseModel):
    quotation_approval_id: UUID
    quotation_id: UUID
    quotation_revision_id: UUID
    status: Literal["approved"]
    approved_by: str
    maker_subject: str
    required_exceptions: list[str]
    exceptions: list[QuotationApprovalExceptionResponse]


class ConvertQuotationCommand(CommandModel):
    sales_order_id: UUID


class QuotationConversionResponse(BaseModel):
    quotation_id: UUID
    sales_order_id: UUID
    sales_order_revision_id: UUID
    status: Literal["converted"]


@dataclass(frozen=True, slots=True)
class DraftContext:
    customer: RowMapping
    address: RowMapping
    currency: str
    payment_policy: PaymentTimingPolicy
    payment_override_reason: str | None
    payment_overridden_by: str | None


@dataclass(frozen=True, slots=True)
class CalculatedLine:
    response: QuotationLineResponse
    database_values: dict[str, object]


@dataclass(frozen=True, slots=True)
class LineCalculationInput:
    command: QuotationLineInput
    position: int
    price: RowMapping
    sku: RowMapping
    factor: Decimal
    quantity_base: Decimal
    conversion_id: UUID | None
    conversion_version: int | None
    conversion_effective_from: date | None
    conversion_effective_to: date | None
    effective_price: Decimal
    pre_discount: Decimal


def _request_hash(operation: str, command: BaseModel, context: str = "") -> str:
    payload = f"{operation}:{context}:{command.model_dump_json(exclude_none=False)}"
    return sha256(payload.encode()).hexdigest()


def _money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(currency_quantum(currency), ROUND_HALF_UP)


async def _allocate_quotation_number(
    session: AsyncSession,
    branch_id: UUID,
) -> tuple[UUID, int, str]:
    series = (
        (
            await session.execute(
                select(document_series)
                .where(
                    document_series.c.branch_id == branch_id,
                    document_series.c.document_type == "quotation",
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if series is None:
        raise AppError(
            503,
            "quotation_series_not_configured",
            "Quotation document series is not configured for this branch.",
        )
    series_id = cast(UUID, series["document_series_id"])
    series_number = cast(int, series["next_number"])
    prefix = cast(str, series["prefix"])
    await session.execute(
        update(document_series)
        .where(document_series.c.document_series_id == series_id)
        .values(next_number=document_series.c.next_number + 1)
    )
    return series_id, series_number, f"{prefix}-{series_number:08d}"


async def _audit_quotation_number(
    session: AsyncSession,
    series_id: UUID,
    series_number: int,
    quotation_id: UUID,
) -> None:
    await session.execute(
        insert(document_series_number_audit).values(
            document_series_number_audit_id=uuid4(),
            document_series_id=series_id,
            series_number=series_number,
            status="issued",
            quotation_id=quotation_id,
        )
    )


async def _load_draft_context(
    session: AsyncSession,
    command: QuotationDraftFields,
    actor: AuthorizedUser,
) -> DraftContext:
    if command.branch_id not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    customer = (
        (
            await session.execute(
                select(customer_accounts).where(
                    customer_accounts.c.customer_id == command.customer_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if customer is None:
        raise AppError(404, "customer_not_found", "The Customer Account does not exist.")
    if customer["branch_id"] != command.branch_id:
        raise AppError(
            409,
            "customer_branch_conflict",
            "The Customer Account does not belong to the Quotation Branch.",
        )
    if customer["version"] != command.expected_customer_version:
        raise AppError(
            409,
            "reference_data_conflict",
            "The Customer Account changed after quotation-entry references were loaded.",
        )
    if customer["status"] != "active":
        raise AppError(409, "customer_inactive", "The Customer Account is not active.")
    address = (
        (
            await session.execute(
                select(customer_address_versions).where(
                    customer_address_versions.c.address_version_id
                    == command.delivery_address_version_id,
                    customer_address_versions.c.customer_id == command.customer_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if address is None or address["kind"] != "delivery":
        raise AppError(
            404,
            "delivery_address_not_found",
            "The Customer delivery address version does not exist.",
        )
    if not address["is_current"]:
        raise AppError(
            409,
            "delivery_address_not_current",
            "A new quotation revision requires a current delivery address.",
        )
    currency = await session.scalar(select(companies.c.base_currency))
    if currency is None:
        raise AppError(409, "base_currency_not_configured", "Base Currency is not configured.")
    selected_policy = cast(
        PaymentTimingPolicy,
        command.payment_timing_policy or customer["payment_timing_policy"],
    )
    is_override = selected_policy != customer["payment_timing_policy"]
    if is_override:
        if "sales:payment-timing-override" not in actor.capabilities:
            raise AppError(
                403,
                "payment_timing_override_required",
                "Payment Timing override capability is required.",
            )
        if command.payment_timing_override_reason is None:
            raise AppError(
                422,
                "payment_timing_override_reason_required",
                "A Payment Timing override requires a reason.",
            )
    elif command.payment_timing_override_reason is not None:
        raise AppError(
            422,
            "payment_timing_override_not_applicable",
            "An override reason requires a policy different from the Customer default.",
        )
    return DraftContext(
        customer=customer,
        address=address,
        currency=currency,
        payment_policy=selected_policy,
        payment_override_reason=command.payment_timing_override_reason if is_override else None,
        payment_overridden_by=actor.subject if is_override else None,
    )


async def _effective_price_list(
    session: AsyncSession,
    *,
    branch_id: UUID,
    customer_id: UUID,
    pricing_date: date,
) -> RowMapping:
    def statement(customer: UUID | None) -> Select[Any]:
        return (
            select(
                price_lists.c.price_list_id,
                price_lists.c.code,
                price_lists.c.customer_id,
                price_list_versions,
            )
            .select_from(
                price_lists.join(
                    price_list_versions,
                    price_lists.c.price_list_id == price_list_versions.c.price_list_id,
                )
            )
            .where(
                price_lists.c.branch_id == branch_id,
                price_lists.c.customer_id == customer
                if customer is not None
                else price_lists.c.customer_id.is_(None),
                price_lists.c.is_active.is_(True),
                price_list_versions.c.effective_from <= pricing_date,
                or_(
                    price_list_versions.c.effective_to.is_(None),
                    price_list_versions.c.effective_to >= pricing_date,
                ),
            )
        )

    customer_list = (await session.execute(statement(customer_id))).mappings().one_or_none()
    selected = customer_list
    if selected is None:
        selected = (await session.execute(statement(None))).mappings().one_or_none()
    if selected is None:
        raise AppError(
            422,
            "price_list_not_found",
            "No effective Customer or Branch Price List exists.",
        )
    return selected


async def _calculate_quotation(
    session: AsyncSession,
    *,
    command: QuotationDraftFields,
    context: DraftContext,
    actor: AuthorizedUser,
    quotation_id: UUID,
    version: int,
    idempotency_key: str,
    correlation_id: str,
) -> tuple[QuotationResponse, UUID, list[CalculatedLine], dict[str, object]]:
    if any(line.manual_override_unit_price is not None for line in command.lines):
        if "sales:price-override" not in actor.capabilities:
            raise AppError(
                403,
                "price_override_required",
                "Price override capability is required.",
            )
    pricing_date = date.today()
    if command.expected_pricing_date != pricing_date:
        raise AppError(
            409,
            "reference_data_conflict",
            "The pricing date changed after quotation-entry references were loaded.",
        )
    price_list = await _effective_price_list(
        session,
        branch_id=command.branch_id,
        customer_id=command.customer_id,
        pricing_date=pricing_date,
    )
    if price_list["price_list_version_id"] != command.expected_price_list_version_id:
        raise AppError(
            409,
            "reference_data_conflict",
            "The effective Price List changed after quotation-entry references were loaded.",
        )
    if price_list["currency"] != context.currency:
        raise AppError(
            409,
            "price_list_currency_conflict",
            "Quotation Price Lists must use the Company Base Currency.",
        )
    price_rows = (
        (
            await session.execute(
                select(
                    price_list_lines,
                    tax_code_versions.c.rate.label("tax_rate"),
                    tax_code_versions.c.version.label("tax_version"),
                    tax_codes.c.code.label("tax_code"),
                )
                .select_from(
                    price_list_lines.join(
                        tax_code_versions,
                        price_list_lines.c.tax_code_version_id
                        == tax_code_versions.c.tax_code_version_id,
                    ).join(
                        tax_codes,
                        tax_code_versions.c.tax_code_id == tax_codes.c.tax_code_id,
                    )
                )
                .where(
                    price_list_lines.c.price_list_version_id == price_list["price_list_version_id"]
                )
            )
        )
        .mappings()
        .all()
    )
    prices = {(row["sku_id"], row["unit_code"]): row for row in price_rows}
    line_inputs: list[LineCalculationInput] = []
    quantum = currency_quantum(context.currency)
    for position, line in enumerate(command.lines, start=1):
        price = prices.get((line.sku_id, line.unit_code))
        if price is None:
            raise AppError(
                422,
                "price_not_found",
                "The effective Price List does not contain the requested SKU and unit.",
            )
        if price["price_list_line_id"] != line.expected_price_list_line_id:
            raise AppError(
                409,
                "reference_data_conflict",
                "A Price List line changed after quotation-entry references were loaded.",
            )
        sku = (
            (await session.execute(select(skus).where(skus.c.sku_id == line.sku_id)))
            .mappings()
            .one_or_none()
        )
        if sku is None or not sku["is_active"]:
            raise AppError(404, "sku_not_found", "The active SKU does not exist.")
        factor = Decimal("1")
        conversion_id: UUID | None = None
        conversion_version: int | None = None
        conversion_effective_from: date | None = None
        conversion_effective_to: date | None = None
        if line.unit_code != sku["base_stocking_unit"]:
            conversion = (
                (
                    await session.execute(
                        select(unit_conversions).where(
                            unit_conversions.c.sku_id == line.sku_id,
                            unit_conversions.c.unit_code == line.unit_code,
                            unit_conversions.c.effective_from <= pricing_date,
                            or_(
                                unit_conversions.c.effective_to.is_(None),
                                unit_conversions.c.effective_to >= pricing_date,
                            ),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if conversion is None:
                raise AppError(
                    422,
                    "unit_conversion_not_effective",
                    "No effective Unit Conversion exists for the Quotation date.",
                )
            factor = conversion["base_quantity"]
            conversion_id = conversion["unit_conversion_id"]
            conversion_version = conversion["version"]
            conversion_effective_from = conversion["effective_from"]
            conversion_effective_to = conversion["effective_to"]
        if (
            conversion_id != line.expected_unit_conversion_id
            or conversion_version != line.expected_unit_conversion_version
        ):
            raise AppError(
                409,
                "reference_data_conflict",
                "A Unit Conversion changed after quotation-entry references were loaded.",
            )
        with localcontext() as calculation_context:
            calculation_context.prec = 50
            quantity_base = (line.quantity * factor).quantize(SIX_PLACES, ROUND_HALF_UP)
        if quantity_base > Decimal("999999999999.999999"):
            raise AppError(
                422,
                "calculated_quantity_overflow",
                "The converted base quantity exceeds the supported range.",
            )
        effective_price = (
            line.manual_override_unit_price
            if line.manual_override_unit_price is not None
            else price["list_unit_price"]
        )
        with localcontext() as calculation_context:
            calculation_context.prec = 50
            extended_price = line.quantity * effective_price
        if extended_price > Decimal("999999999999999999.999999"):
            raise AppError(
                422,
                "calculated_amount_overflow",
                "The extended line amount exceeds the supported range.",
            )
        pre_discount = _money(extended_price, context.currency)
        line_inputs.append(
            LineCalculationInput(
                command=line,
                position=position,
                price=price,
                sku=sku,
                factor=factor,
                quantity_base=quantity_base,
                conversion_id=conversion_id,
                conversion_version=conversion_version,
                conversion_effective_from=conversion_effective_from,
                conversion_effective_to=conversion_effective_to,
                effective_price=effective_price,
                pre_discount=pre_discount,
            )
        )
    subtotal = sum(
        (item.pre_discount for item in line_inputs),
        Decimal("0").quantize(quantum),
    )
    discount_total = _money(command.order_discount_amount, context.currency)
    if discount_total > subtotal:
        raise AppError(
            422,
            "discount_exceeds_value",
            "The quotation discount cannot exceed the pre-discount value.",
        )
    allocations = allocate_largest_remainder(
        amount=discount_total,
        weighted_lines=[
            (item.position, item.command.line_id, item.pre_discount) for item in line_inputs
        ],
        quantum=quantum,
    )
    calculated_lines: list[CalculatedLine] = []
    response_lines: list[QuotationLineResponse] = []
    for item in line_inputs:
        line = item.command
        price = item.price
        allocated = allocations[line.line_id]
        after_discount = _money(item.pre_discount - allocated, context.currency)
        tax_rate = price["tax_rate"]
        if price_list["inclusion_mode"] == "exclusive":
            taxable = after_discount
            tax_amount = _money(taxable * tax_rate, context.currency)
            line_total = _money(taxable + tax_amount, context.currency)
        else:
            line_total = after_discount
            taxable = _money(line_total / (Decimal("1") + tax_rate), context.currency)
            tax_amount = _money(line_total - taxable, context.currency)
        conversion_snapshot = {
            "entered_quantity": str(line.quantity),
            "entered_unit": line.unit_code,
            "base_quantity_per_unit": str(item.factor),
            "base_quantity": str(item.quantity_base),
            "unit_conversion_id": ("" if item.conversion_id is None else str(item.conversion_id)),
            "unit_conversion_version": (
                "" if item.conversion_version is None else str(item.conversion_version)
            ),
            "effective_from": (
                ""
                if item.conversion_effective_from is None
                else str(item.conversion_effective_from)
            ),
            "effective_to": (
                "" if item.conversion_effective_to is None else str(item.conversion_effective_to)
            ),
        }
        tax_snapshot = {
            "tax_code": price["tax_code"],
            "tax_code_version_id": str(price["tax_code_version_id"]),
            "tax_code_version": str(price["tax_version"]),
            "tax_rate": str(tax_rate),
            "inclusion_mode": price_list["inclusion_mode"],
            "taxable_basis": str(taxable),
            "tax_amount": str(tax_amount),
        }
        calculation_snapshot = {
            "contract_version": "1",
            "entered_quantity": str(line.quantity),
            "effective_unit_price": str(item.effective_price),
            "pre_discount_amount": str(item.pre_discount),
            "allocated_discount": str(allocated),
            "taxable_amount": str(taxable),
            "tax_amount": str(tax_amount),
            "line_total": str(line_total),
            "currency_quantum": str(quantum),
            "rounding": "ROUND_HALF_UP",
        }
        below_floor = (
            price["floor_unit_price"] is not None
            and item.effective_price < price["floor_unit_price"]
        )
        price_source: Literal["customer", "branch"] = (
            "customer" if price_list["customer_id"] is not None else "branch"
        )
        response_line = QuotationLineResponse(
            line_id=line.line_id,
            line_position=item.position,
            sku_id=line.sku_id,
            sku_code=item.sku["code"],
            sku_name=item.sku["name"],
            entered_quantity=line.quantity,
            entered_unit=line.unit_code,
            quantity_base=item.quantity_base,
            conversion_snapshot=conversion_snapshot,
            price_list_line_id=price["price_list_line_id"],
            price_list_code=price_list["code"],
            price_list_version_id=price_list["price_list_version_id"],
            price_list_version=price_list["version"],
            price_source=price_source,
            list_unit_price=price["list_unit_price"],
            floor_unit_price=price["floor_unit_price"],
            manual_override_unit_price=line.manual_override_unit_price,
            price_override_reason=line.price_override_reason,
            effective_unit_price=item.effective_price,
            allocated_discount=allocated,
            tax_snapshot=tax_snapshot,
            calculation_snapshot=calculation_snapshot,
            taxable_amount=taxable,
            tax_amount=tax_amount,
            line_total=line_total,
            below_floor=below_floor,
        )
        response_lines.append(response_line)
        calculated_lines.append(
            CalculatedLine(
                response=response_line,
                database_values={
                    "quotation_line_revision_id": uuid4(),
                    "line_id": line.line_id,
                    "line_position": item.position,
                    "sku_id": line.sku_id,
                    "sku_code": item.sku["code"],
                    "sku_name": item.sku["name"],
                    "entered_quantity": line.quantity,
                    "entered_unit": line.unit_code,
                    "quantity_base": item.quantity_base,
                    "conversion_snapshot": conversion_snapshot,
                    "price_list_line_id": price["price_list_line_id"],
                    "list_unit_price": price["list_unit_price"],
                    "floor_unit_price": price["floor_unit_price"],
                    "manual_override_unit_price": line.manual_override_unit_price,
                    "price_override_reason": line.price_override_reason,
                    "effective_unit_price": item.effective_price,
                    "price_source": price_source,
                    "below_floor": below_floor,
                    "allocated_discount": allocated,
                    "tax_snapshot": tax_snapshot,
                    "calculation_snapshot": calculation_snapshot,
                    "taxable_amount": taxable,
                    "tax_amount": tax_amount,
                    "line_total": line_total,
                },
            )
        )
    taxable_total = sum((line.taxable_amount for line in response_lines), Decimal("0"))
    tax_total = sum((line.tax_amount for line in response_lines), Decimal("0"))
    grand_total = sum((line.line_total for line in response_lines), Decimal("0"))
    address_snapshot = {
        "address_key": context.address["address_key"],
        "version": context.address["version"],
        "kind": context.address["kind"],
        "line_1": context.address["line_1"],
        "line_2": context.address["line_2"],
        "city": context.address["city"],
        "region": context.address["region"],
        "postal_code": context.address["postal_code"],
        "country_code": context.address["country_code"],
    }
    revision_id = uuid4()
    revision_values: dict[str, object] = {
        "quotation_revision_id": revision_id,
        "quotation_id": quotation_id,
        "version": version,
        "branch_id": command.branch_id,
        "customer_id": command.customer_id,
        "customer_version": context.customer["version"],
        "delivery_address_version_id": command.delivery_address_version_id,
        "delivery_address_snapshot": address_snapshot,
        "currency": context.currency,
        "price_inclusion_mode": price_list["inclusion_mode"],
        "price_list_version_id": price_list["price_list_version_id"],
        "price_list_code": price_list["code"],
        "price_list_version": price_list["version"],
        "pricing_date": pricing_date,
        "payment_timing_default": context.customer["payment_timing_policy"],
        "payment_timing_policy": context.payment_policy,
        "payment_timing_override_reason": context.payment_override_reason,
        "payment_timing_overridden_by": context.payment_overridden_by,
        "order_discount_amount": command.order_discount_amount,
        "subtotal": subtotal,
        "discount_total": discount_total,
        "taxable_total": taxable_total,
        "tax_total": tax_total,
        "grand_total": grand_total,
        "expiry_date": command.expiry_date,
        "calculation_contract_version": 1,
        "actor_subject": actor.subject,
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
    }
    result = QuotationResponse(
        quotation_id=quotation_id,
        number="",
        status="draft",
        version=version,
        branch_id=command.branch_id,
        customer_id=command.customer_id,
        customer_version=context.customer["version"],
        delivery_address_version_id=command.delivery_address_version_id,
        delivery_address_snapshot=address_snapshot,
        currency=context.currency,
        price_inclusion_mode=price_list["inclusion_mode"],
        price_list_version_id=price_list["price_list_version_id"],
        price_list_code=price_list["code"],
        price_list_version=price_list["version"],
        pricing_date=pricing_date,
        expiry_date=command.expiry_date,
        payment_timing_default=context.customer["payment_timing_policy"],
        payment_timing_policy=context.payment_policy,
        payment_timing_override_reason=context.payment_override_reason,
        payment_timing_overridden_by=context.payment_overridden_by,
        order_discount_amount=command.order_discount_amount,
        subtotal=subtotal,
        discount_total=discount_total,
        taxable_total=taxable_total,
        tax_total=tax_total,
        grand_total=grand_total,
        calculation_contract_version=1,
        converted_sales_order_id=None,
        lines=response_lines,
    )
    return result, revision_id, calculated_lines, revision_values


async def _persist_revision(
    session: AsyncSession,
    *,
    revision_id: UUID,
    revision_values: dict[str, object],
    calculated_lines: list[CalculatedLine],
) -> None:
    await session.execute(insert(quotation_revisions).values(**revision_values))
    for line in calculated_lines:
        await session.execute(
            insert(quotation_line_revisions).values(
                quotation_revision_id=revision_id,
                **line.database_values,
            )
        )


async def _load_quotation_response(
    session: AsyncSession,
    quotation_id: UUID,
    actor: AuthorizedUser,
) -> QuotationResponse:
    quotation = (
        (await session.execute(select(quotations).where(quotations.c.quotation_id == quotation_id)))
        .mappings()
        .one_or_none()
    )
    if quotation is None:
        raise AppError(404, "quotation_not_found", "The Quotation does not exist.")
    if quotation["branch_id"] not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    revision = (
        (
            await session.execute(
                select(quotation_revisions).where(
                    quotation_revisions.c.quotation_id == quotation_id,
                    quotation_revisions.c.version == quotation["version"],
                )
            )
        )
        .mappings()
        .one()
    )
    rows = (
        (
            await session.execute(
                select(quotation_line_revisions)
                .where(
                    quotation_line_revisions.c.quotation_revision_id
                    == revision["quotation_revision_id"]
                )
                .order_by(quotation_line_revisions.c.line_position)
            )
        )
        .mappings()
        .all()
    )
    currency = revision["currency"]
    lines = [
        QuotationLineResponse(
            line_id=row["line_id"],
            line_position=row["line_position"],
            sku_id=row["sku_id"],
            sku_code=row["sku_code"],
            sku_name=row["sku_name"],
            entered_quantity=row["entered_quantity"],
            entered_unit=row["entered_unit"],
            quantity_base=row["quantity_base"],
            conversion_snapshot=row["conversion_snapshot"],
            price_list_line_id=row["price_list_line_id"],
            price_list_code=revision["price_list_code"],
            price_list_version_id=revision["price_list_version_id"],
            price_list_version=revision["price_list_version"],
            price_source=row["price_source"],
            list_unit_price=row["list_unit_price"],
            floor_unit_price=row["floor_unit_price"],
            manual_override_unit_price=row["manual_override_unit_price"],
            price_override_reason=row["price_override_reason"],
            effective_unit_price=row["effective_unit_price"],
            allocated_discount=_money(row["allocated_discount"], currency),
            tax_snapshot=row["tax_snapshot"],
            calculation_snapshot=row["calculation_snapshot"],
            taxable_amount=_money(row["taxable_amount"], currency),
            tax_amount=_money(row["tax_amount"], currency),
            line_total=_money(row["line_total"], currency),
            below_floor=row["below_floor"],
        )
        for row in rows
    ]
    return QuotationResponse(
        quotation_id=quotation_id,
        number=quotation["number"],
        status=quotation["status"],
        version=revision["version"],
        branch_id=revision["branch_id"],
        customer_id=revision["customer_id"],
        customer_version=revision["customer_version"],
        delivery_address_version_id=revision["delivery_address_version_id"],
        delivery_address_snapshot=revision["delivery_address_snapshot"],
        currency=currency,
        price_inclusion_mode=revision["price_inclusion_mode"],
        price_list_version_id=revision["price_list_version_id"],
        price_list_code=revision["price_list_code"],
        price_list_version=revision["price_list_version"],
        pricing_date=revision["pricing_date"],
        expiry_date=revision["expiry_date"],
        payment_timing_default=revision["payment_timing_default"],
        payment_timing_policy=revision["payment_timing_policy"],
        payment_timing_override_reason=revision["payment_timing_override_reason"],
        payment_timing_overridden_by=revision["payment_timing_overridden_by"],
        order_discount_amount=revision["order_discount_amount"],
        subtotal=_money(revision["subtotal"], currency),
        discount_total=_money(revision["discount_total"], currency),
        taxable_total=_money(revision["taxable_total"], currency),
        tax_total=_money(revision["tax_total"], currency),
        grand_total=_money(revision["grand_total"], currency),
        calculation_contract_version=revision["calculation_contract_version"],
        converted_sales_order_id=quotation["converted_sales_order_id"],
        lines=lines,
    )


@router.post(
    "",
    response_model=QuotationResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500, 503),
)
async def create_quotation(
    command: CreateQuotationCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_quotation_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> QuotationResponse:
    if command.branch_id not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    request_hash = _request_hash("create_quotation", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        return QuotationResponse.model_validate(replay)
    context = await _load_draft_context(session, command, actor)
    quotation_id = uuid4()
    result, revision_id, lines, revision_values = await _calculate_quotation(
        session,
        command=command,
        context=context,
        actor=actor,
        quotation_id=quotation_id,
        version=1,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    series_id, series_number, number = await _allocate_quotation_number(session, command.branch_id)
    result = result.model_copy(update={"number": number})
    try:
        await session.execute(
            insert(quotations).values(
                quotation_id=quotation_id,
                branch_id=command.branch_id,
                customer_id=command.customer_id,
                status="draft",
                version=1,
                document_series_id=series_id,
                series_number=series_number,
                number=number,
                expiry_date=command.expiry_date,
                created_by=actor.subject,
                updated_by=actor.subject,
            )
        )
        await _persist_revision(
            session,
            revision_id=revision_id,
            revision_values=revision_values,
            calculated_lines=lines,
        )
        await _audit_quotation_number(
            session, series_id=series_id, series_number=series_number, quotation_id=quotation_id
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        await session.commit()
        return result
    except IntegrityError as error:
        await session.rollback()
        raise AppError(
            409,
            "quotation_draft_conflict",
            "The Quotation identity or revision already exists.",
        ) from error


@router.put(
    "/{quotation_id}",
    response_model=QuotationResponse,
    responses=error_responses(401, 403, 404, 409, 422, 500, 503),
)
async def update_quotation(
    quotation_id: UUID,
    command: UpdateQuotationCommand,
    request: Request,
    actor: Annotated[AuthorizedUser, Depends(require_quotation_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> QuotationResponse:
    existing = (
        (await session.execute(select(quotations).where(quotations.c.quotation_id == quotation_id)))
        .mappings()
        .one_or_none()
    )
    if existing is None:
        raise AppError(404, "quotation_not_found", "The Quotation does not exist.")
    if existing["branch_id"] not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    request_hash = _request_hash(
        "update_quotation",
        command,
        context=f"{quotation_id}:{if_match}",
    )
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return QuotationResponse.model_validate(replay)
    locked = (
        (
            await session.execute(
                select(quotations)
                .where(quotations.c.quotation_id == quotation_id)
                .with_for_update()
            )
        )
        .mappings()
        .one()
    )
    if locked["version"] != if_match:
        raise AppError(
            409,
            "optimistic_version_conflict",
            "The Quotation has changed and requires explicit review.",
        )
    if locked["status"] not in ("draft", "approved"):
        raise AppError(
            409,
            "quotation_not_editable",
            "Only draft or approved Quotations can be revised.",
        )
    context = await _load_draft_context(session, command, actor)
    next_version = if_match + 1
    result, revision_id, lines, revision_values = await _calculate_quotation(
        session,
        command=command,
        context=context,
        actor=actor,
        quotation_id=quotation_id,
        version=next_version,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    try:
        updated = await session.execute(
            update(quotations)
            .where(
                quotations.c.quotation_id == quotation_id,
                quotations.c.version == if_match,
            )
            .values(
                branch_id=command.branch_id,
                customer_id=command.customer_id,
                status="draft",
                approved_revision_id=None,
                converted_sales_order_id=None,
                version=next_version,
                expiry_date=command.expiry_date,
                updated_by=actor.subject,
                updated_at=func.now(),
            )
            .returning(quotations.c.quotation_id)
        )
        if updated.scalar_one_or_none() is None:
            raise AppError(
                409,
                "optimistic_version_conflict",
                "The Quotation has changed and requires explicit review.",
            )
        await _persist_revision(
            session,
            revision_id=revision_id,
            revision_values=revision_values,
            calculated_lines=lines,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        await session.commit()
        return result
    except IntegrityError as error:
        await session.rollback()
        raise AppError(
            409,
            "quotation_revision_conflict",
            "The Quotation revision conflicts with current server state.",
        ) from error


@router.get(
    "/{quotation_id}",
    response_model=QuotationResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_quotation(
    quotation_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_quotation_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> QuotationResponse:
    return await _load_quotation_response(session, quotation_id, actor)


@router.get(
    "",
    response_model=QuotationSearchResponse,
    responses=error_responses(401, 403, 422, 500),
)
async def search_quotations(
    actor: Annotated[AuthorizedUser, Depends(require_quotation_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    query: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> QuotationSearchResponse:
    if not actor.branch_ids:
        return QuotationSearchResponse(items=[], total=0)
    latest = quotations.join(
        quotation_revisions,
        (quotations.c.quotation_id == quotation_revisions.c.quotation_id)
        & (quotations.c.version == quotation_revisions.c.version),
    ).join(
        customer_accounts,
        quotations.c.customer_id == customer_accounts.c.customer_id,
    )
    filters: list[Any] = [quotations.c.branch_id.in_(actor.branch_ids)]
    if query.strip():
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                customer_accounts.c.legal_name.ilike(pattern),
                customer_accounts.c.account_number.ilike(pattern),
                quotations.c.number.ilike(pattern),
            )
        )
    total = await session.scalar(select(func.count()).select_from(latest).where(*filters))
    rows = (
        await session.execute(
            select(
                quotations.c.quotation_id,
                quotations.c.number,
                quotations.c.status,
                quotations.c.version,
                quotations.c.branch_id,
                quotations.c.customer_id,
                customer_accounts.c.legal_name.label("customer_name"),
                quotation_revisions.c.currency,
                quotation_revisions.c.grand_total,
                quotation_revisions.c.expiry_date,
            )
            .select_from(latest)
            .where(*filters)
            .order_by(quotations.c.updated_at.desc(), quotations.c.quotation_id)
            .limit(limit)
        )
    ).mappings()
    return QuotationSearchResponse(
        items=[
            QuotationSearchItem(
                **row,
                grand_total=_money(row["grand_total"], row["currency"]),
            )
            for row in rows
        ],
        total=total or 0,
    )


async def _authority(
    session: AsyncSession,
    *,
    actor: AuthorizedUser,
    branch_id: UUID,
    capability: str,
    maker_subject: str,
    amount: Decimal,
    percentage: Decimal | None,
) -> dict[str, object]:
    if actor.subject == maker_subject:
        raise AppError(
            409,
            "maker_checker_violation",
            "The quotation maker cannot approve the same commercial exception.",
        )
    if capability not in actor.capabilities:
        raise AppError(
            409,
            "quotation_exception_required",
            f"The quotation requires a different approver with '{capability}' authority.",
        )
    row = (
        (
            await session.execute(
                select(approval_authorities).where(
                    approval_authorities.c.user_subject == actor.subject,
                    approval_authorities.c.capability_code == capability,
                    approval_authorities.c.branch_id == branch_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(
            403,
            "approval_authority_required",
            f"Explicit '{capability}' Approval Authority is required.",
        )
    if row["maximum_amount"] is not None and amount > row["maximum_amount"]:
        raise AppError(
            403,
            "approval_limit_exceeded",
            "The quotation exception exceeds the approver's amount authority.",
        )
    if (
        percentage is not None
        and row["maximum_percentage"] is not None
        and percentage > row["maximum_percentage"]
    ):
        raise AppError(
            403,
            "approval_limit_exceeded",
            "The quotation exception exceeds the approver's percentage authority.",
        )
    return {
        "capability": capability,
        "maximum_amount": (
            str(row["maximum_amount"]) if row["maximum_amount"] is not None else None
        ),
        "maximum_percentage": (
            str(row["maximum_percentage"]) if row["maximum_percentage"] is not None else None
        ),
        "maker_checker_required": row["maker_checker_required"],
    }


@router.post(
    "/{quotation_id}/approval",
    response_model=QuotationApprovalResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def approve_quotation(
    quotation_id: UUID,
    command: ApproveQuotationCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_quotation_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> QuotationApprovalResponse:
    request_hash = _request_hash(
        "approve_quotation",
        command,
        context=f"{quotation_id}:{if_match}",
    )
    await session.rollback()
    async with session.begin():
        scoped = (
            (
                await session.execute(
                    select(
                        quotations.c.branch_id,
                        quotations.c.quotation_id,
                    ).where(quotations.c.quotation_id == quotation_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if scoped is None:
            raise AppError(404, "quotation_not_found", "The Quotation does not exist.")
        if scoped["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return QuotationApprovalResponse.model_validate(replay)
        locked = (
            (
                await session.execute(
                    select(quotations)
                    .where(quotations.c.quotation_id == quotation_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if locked["version"] != if_match:
            raise AppError(
                409,
                "optimistic_version_conflict",
                "The Quotation has changed and requires explicit review.",
            )
        if locked["status"] != "draft":
            raise AppError(
                409,
                "quotation_not_draft",
                "Only draft Quotations can be approved.",
            )
        revision = (
            (
                await session.execute(
                    select(quotation_revisions).where(
                        quotation_revisions.c.quotation_id == quotation_id,
                        quotation_revisions.c.version == if_match,
                    )
                )
            )
            .mappings()
            .one()
        )
        if revision["expiry_date"] <= date.today():
            raise AppError(
                409,
                "quotation_expired",
                "The Quotation has expired and cannot be approved.",
            )
        lines = (
            (
                await session.execute(
                    select(quotation_line_revisions).where(
                        quotation_line_revisions.c.quotation_revision_id
                        == revision["quotation_revision_id"]
                    )
                )
            )
            .mappings()
            .all()
        )
        line_discount_total = sum((line["allocated_discount"] for line in lines), Decimal("0"))
        line_taxable_total = sum((line["taxable_amount"] for line in lines), Decimal("0"))
        line_tax_total = sum((line["tax_amount"] for line in lines), Decimal("0"))
        line_grand_total = sum((line["line_total"] for line in lines), Decimal("0"))
        if (
            line_discount_total != revision["discount_total"]
            or line_taxable_total != revision["taxable_total"]
            or line_tax_total != revision["tax_total"]
            or line_grand_total != revision["grand_total"]
        ):
            raise AppError(
                409,
                "quotation_snapshot_invalid",
                "Stored pricing, tax, or calculation snapshots are inconsistent.",
            )
        maker = revision["actor_subject"]
        if actor.subject == maker:
            raise AppError(
                409,
                "maker_checker_violation",
                "The quotation maker cannot approve the same quotation.",
            )
        customer = (
            (
                await session.execute(
                    select(customer_accounts).where(
                        customer_accounts.c.customer_id == locked["customer_id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        if customer["status"] != "active":
            raise AppError(409, "customer_inactive", "The Customer Account is not active.")
        required: list[str] = []
        exception_rows: list[dict[str, object]] = []
        discount_percentage = (
            (revision["discount_total"] / revision["subtotal"] * Decimal("100"))
            if revision["subtotal"] > Decimal("0")
            else Decimal("0")
        )
        maker_discount_threshold = (
            await session.scalar(
                select(approval_authorities.c.maximum_percentage).where(
                    approval_authorities.c.user_subject == maker,
                    approval_authorities.c.capability_code == "sales:discount-enter",
                    approval_authorities.c.branch_id == locked["branch_id"],
                )
            )
        ) or Decimal("0")
        if discount_percentage > maker_discount_threshold:
            required.append("discount")
            permitted_discount = _money(
                revision["subtotal"] * maker_discount_threshold / Decimal("100"),
                revision["currency"],
            )
            excess_discount = max(
                _money(
                    revision["discount_total"] - permitted_discount,
                    revision["currency"],
                ),
                Decimal("0"),
            )
            authority = await _authority(
                session,
                actor=actor,
                branch_id=locked["branch_id"],
                capability="sales:discount-approve",
                maker_subject=maker,
                amount=excess_discount,
                percentage=discount_percentage,
            )
            if command.exception_reason is None:
                raise AppError(
                    422,
                    "exception_reason_required",
                    "A reason is required for discount approval.",
                )
            exception_rows.append(
                {
                    "exception_type": "discount",
                    "reason": command.exception_reason,
                    "exception_amount": excess_discount,
                    "exception_percentage": discount_percentage,
                    "authority_snapshot": {
                        **authority,
                        "maker_discount_threshold": str(maker_discount_threshold),
                    },
                }
            )
        below_floor_amount = _money(
            sum(
                [
                    _money(
                        max(
                            (line["floor_unit_price"] or Decimal("0")) * line["entered_quantity"]
                            - (
                                line["effective_unit_price"] * line["entered_quantity"]
                                - line["allocated_discount"]
                            ),
                            Decimal("0"),
                        ),
                        revision["currency"],
                    )
                    for line in lines
                ],
                Decimal("0"),
            ),
            revision["currency"],
        )
        if below_floor_amount > Decimal("0"):
            required.append("below_floor")
            authority = await _authority(
                session,
                actor=actor,
                branch_id=locked["branch_id"],
                capability="sales:below-floor-approve",
                maker_subject=maker,
                amount=below_floor_amount,
                percentage=None,
            )
            if command.exception_reason is None:
                raise AppError(
                    422,
                    "exception_reason_required",
                    "A reason is required for below-floor approval.",
                )
            exception_rows.append(
                {
                    "exception_type": "below_floor",
                    "reason": command.exception_reason,
                    "exception_amount": below_floor_amount,
                    "exception_percentage": None,
                    "authority_snapshot": authority,
                }
            )
        approval_id = uuid4()
        await session.execute(
            insert(quotation_approvals).values(
                quotation_approval_id=approval_id,
                quotation_id=quotation_id,
                quotation_revision_id=revision["quotation_revision_id"],
                customer_id=locked["customer_id"],
                maker_subject=maker,
                approved_by=actor.subject,
                order_total=revision["grand_total"],
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        for evidence in exception_rows:
            await session.execute(
                insert(quotation_approval_exceptions).values(
                    exception_approval_id=uuid4(),
                    quotation_approval_id=approval_id,
                    maker_subject=maker,
                    approved_by=actor.subject,
                    **evidence,
                )
            )
        await session.execute(
            update(quotations)
            .where(quotations.c.quotation_id == quotation_id)
            .values(
                status="approved",
                approved_revision_id=revision["quotation_revision_id"],
                updated_by=actor.subject,
                updated_at=func.now(),
            )
        )
        result = QuotationApprovalResponse(
            quotation_approval_id=approval_id,
            quotation_id=quotation_id,
            quotation_revision_id=revision["quotation_revision_id"],
            status="approved",
            approved_by=actor.subject,
            maker_subject=maker,
            required_exceptions=required,
            exceptions=[
                QuotationApprovalExceptionResponse(
                    exception_type=cast(Literal["discount", "below_floor"], row["exception_type"]),
                    amount=cast(Decimal, row["exception_amount"]),
                    percentage=cast(Decimal | None, row["exception_percentage"]),
                )
                for row in exception_rows
            ],
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result


@router.post(
    "/{quotation_id}/convert",
    response_model=QuotationConversionResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def convert_quotation(
    quotation_id: UUID,
    command: ConvertQuotationCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_quotation_converter)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> QuotationConversionResponse:
    request_hash = _request_hash(
        "convert_quotation",
        command,
        context=f"{quotation_id}:{if_match}",
    )
    await session.rollback()
    async with session.begin():
        scoped = (
            (
                await session.execute(
                    select(
                        quotations.c.branch_id,
                        quotations.c.quotation_id,
                    ).where(quotations.c.quotation_id == quotation_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if scoped is None:
            raise AppError(404, "quotation_not_found", "The Quotation does not exist.")
        if scoped["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return QuotationConversionResponse.model_validate(replay)
        locked = (
            (
                await session.execute(
                    select(quotations)
                    .where(quotations.c.quotation_id == quotation_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if locked["version"] != if_match:
            raise AppError(
                409,
                "optimistic_version_conflict",
                "The Quotation has changed and requires explicit review.",
            )
        if locked["status"] != "approved":
            raise AppError(
                409,
                "quotation_not_approved",
                "Only approved Quotations can be converted to a Sales Order Draft.",
            )
        if locked["approved_revision_id"] is None:
            raise AppError(
                409,
                "quotation_not_approved",
                "The Quotation has no approved revision.",
            )
        revision = (
            (
                await session.execute(
                    select(quotation_revisions).where(
                        quotation_revisions.c.quotation_revision_id
                        == locked["approved_revision_id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        if revision["quotation_revision_id"] != locked["approved_revision_id"]:
            raise AppError(
                409,
                "quotation_superseded",
                "The approved Quotation revision has been superseded.",
            )
        if revision["expiry_date"] <= date.today():
            raise AppError(
                409,
                "quotation_expired",
                "The Quotation has expired and cannot be converted.",
            )
        existing_conversion = await session.scalar(
            select(quotation_conversion_events.c.sales_order_id).where(
                quotation_conversion_events.c.quotation_id == quotation_id
            )
        )
        if existing_conversion is not None:
            raise AppError(
                409,
                "quotation_already_converted",
                "The Quotation has already been converted to a Sales Order.",
            )
        customer = (
            (
                await session.execute(
                    select(customer_accounts).where(
                        customer_accounts.c.customer_id == locked["customer_id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        if customer["status"] != "active":
            raise AppError(409, "customer_inactive", "The Customer Account is not active.")
        if customer["version"] != revision["customer_version"]:
            raise AppError(
                409,
                "reference_data_conflict",
                "The Customer Account changed after this Quotation was approved.",
            )
        sales_order_id = command.sales_order_id
        sales_order_revision_id = uuid4()
        await session.execute(
            insert(sales_orders).values(
                sales_order_id=sales_order_id,
                branch_id=revision["branch_id"],
                customer_id=revision["customer_id"],
                status="draft",
                version=1,
                created_by=actor.subject,
                updated_by=actor.subject,
            )
        )
        await session.execute(
            insert(sales_order_revisions).values(
                sales_order_revision_id=sales_order_revision_id,
                sales_order_id=sales_order_id,
                version=1,
                branch_id=revision["branch_id"],
                customer_id=revision["customer_id"],
                customer_version=revision["customer_version"],
                delivery_address_version_id=revision["delivery_address_version_id"],
                delivery_address_snapshot=revision["delivery_address_snapshot"],
                currency=revision["currency"],
                price_inclusion_mode=revision["price_inclusion_mode"],
                price_list_version_id=revision["price_list_version_id"],
                price_list_code=revision["price_list_code"],
                price_list_version=revision["price_list_version"],
                pricing_date=revision["pricing_date"],
                payment_timing_default=revision["payment_timing_default"],
                payment_timing_policy=revision["payment_timing_policy"],
                payment_timing_override_reason=revision["payment_timing_override_reason"],
                payment_timing_overridden_by=revision["payment_timing_overridden_by"],
                order_discount_amount=revision["order_discount_amount"],
                subtotal=revision["subtotal"],
                discount_total=revision["discount_total"],
                taxable_total=revision["taxable_total"],
                tax_total=revision["tax_total"],
                grand_total=revision["grand_total"],
                calculation_contract_version=revision["calculation_contract_version"],
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        line_rows = (
            (
                await session.execute(
                    select(quotation_line_revisions).where(
                        quotation_line_revisions.c.quotation_revision_id
                        == revision["quotation_revision_id"]
                    )
                )
            )
            .mappings()
            .all()
        )
        for line in line_rows:
            await session.execute(
                insert(sales_order_line_revisions).values(
                    sales_order_revision_id=sales_order_revision_id,
                    sales_order_line_revision_id=uuid4(),
                    line_id=line["line_id"],
                    line_position=line["line_position"],
                    sku_id=line["sku_id"],
                    sku_code=line["sku_code"],
                    sku_name=line["sku_name"],
                    entered_quantity=line["entered_quantity"],
                    entered_unit=line["entered_unit"],
                    quantity_base=line["quantity_base"],
                    conversion_snapshot=line["conversion_snapshot"],
                    price_list_line_id=line["price_list_line_id"],
                    list_unit_price=line["list_unit_price"],
                    floor_unit_price=line["floor_unit_price"],
                    manual_override_unit_price=line["manual_override_unit_price"],
                    price_override_reason=line["price_override_reason"],
                    effective_unit_price=line["effective_unit_price"],
                    price_source=line["price_source"],
                    below_floor=line["below_floor"],
                    allocated_discount=line["allocated_discount"],
                    tax_snapshot=line["tax_snapshot"],
                    calculation_snapshot=line["calculation_snapshot"],
                    taxable_amount=line["taxable_amount"],
                    tax_amount=line["tax_amount"],
                    line_total=line["line_total"],
                )
            )
        await session.execute(
            insert(quotation_conversion_events).values(
                conversion_event_id=uuid4(),
                quotation_id=quotation_id,
                quotation_revision_id=revision["quotation_revision_id"],
                sales_order_id=sales_order_id,
                sales_order_revision_id=sales_order_revision_id,
                converted_by=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        await session.execute(
            update(quotations)
            .where(quotations.c.quotation_id == quotation_id)
            .values(
                status="converted",
                converted_sales_order_id=sales_order_id,
                updated_by=actor.subject,
                updated_at=func.now(),
            )
        )
        result = QuotationConversionResponse(
            quotation_id=quotation_id,
            sales_order_id=sales_order_id,
            sales_order_revision_id=sales_order_revision_id,
            status="converted",
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result
