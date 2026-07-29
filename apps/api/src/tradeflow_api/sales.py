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
from sqlalchemy.sql.elements import ColumnElement

from tradeflow_api.auth import (
    AuthorizedUser,
    require_sales_order_reader,
    require_sales_order_writer,
    require_sales_pricing_writer,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    companies,
    customer_accounts,
    customer_address_versions,
    price_list_lines,
    price_list_versions,
    price_lists,
    sales_order_line_revisions,
    sales_order_revisions,
    sales_orders,
    skus,
    tax_code_versions,
    tax_codes,
    unit_conversions,
)
from tradeflow_api.money import allocate_largest_remainder, currency_quantum

router = APIRouter(prefix="/v1/sales", tags=["sales"])
SIX_PLACES = Decimal("0.000001")
PaymentTimingPolicy = Literal["prepaid", "cash_on_delivery", "on_account"]
PriceInclusionMode = Literal["inclusive", "exclusive"]


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EffectiveCommand(CommandModel):
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def valid_effective_range(self) -> EffectiveCommand:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from.")
        return self


class CreateTaxCodeVersionCommand(EffectiveCommand):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,29}$")
    name: str = Field(min_length=1, max_length=200)
    rate: Decimal = Field(ge=0, le=1, max_digits=9, decimal_places=6)


class TaxCodeVersionResponse(BaseModel):
    tax_code_id: UUID
    tax_code_version_id: UUID
    code: str
    name: str
    version: int
    rate: Decimal
    effective_from: date
    effective_to: date | None


class PriceListLineInput(CommandModel):
    sku_id: UUID
    unit_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")
    list_unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    floor_unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    tax_code_version_id: UUID


class CreatePriceListVersionCommand(EffectiveCommand):
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{1,49}$")
    branch_id: UUID
    customer_id: UUID | None = None
    inclusion_mode: PriceInclusionMode
    items: list[PriceListLineInput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_sku_units(self) -> CreatePriceListVersionCommand:
        keys = [(item.sku_id, item.unit_code) for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("Price List SKU and unit pairs must be unique.")
        return self


class PriceListLineResponse(BaseModel):
    price_list_line_id: UUID
    sku_id: UUID
    unit_code: str
    list_unit_price: Decimal
    floor_unit_price: Decimal | None
    tax_code_version_id: UUID


class PriceListVersionResponse(BaseModel):
    price_list_id: UUID
    price_list_version_id: UUID
    code: str
    version: int
    branch_id: UUID
    customer_id: UUID | None
    currency: str
    inclusion_mode: PriceInclusionMode
    effective_from: date
    effective_to: date | None
    items: list[PriceListLineResponse]


class SalesOrderLineInput(CommandModel):
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
    def override_requires_reason(self) -> SalesOrderLineInput:
        if self.manual_override_unit_price is not None and self.price_override_reason is None:
            raise ValueError("A manual Price Override requires a reason.")
        if self.manual_override_unit_price is None and self.price_override_reason is not None:
            raise ValueError("A Price Override reason requires an override price.")
        return self


class SalesOrderDraftFields(CommandModel):
    branch_id: UUID
    customer_id: UUID
    expected_customer_version: int = Field(ge=1)
    expected_price_list_version_id: UUID
    expected_pricing_date: date
    delivery_address_version_id: UUID
    payment_timing_policy: PaymentTimingPolicy | None = None
    payment_timing_override_reason: str | None = Field(default=None, min_length=1, max_length=500)
    order_discount_amount: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=18, decimal_places=6
    )
    lines: list[SalesOrderLineInput] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_line_ids(self) -> SalesOrderDraftFields:
        line_ids = [line.line_id for line in self.lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("Sales Order Line identities must be unique.")
        return self


class CreateSalesOrderDraftCommand(SalesOrderDraftFields):
    sales_order_id: UUID


class UpdateSalesOrderDraftCommand(SalesOrderDraftFields):
    pass


class SalesOrderLineResponse(BaseModel):
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


class SalesOrderDraftResponse(BaseModel):
    sales_order_id: UUID
    status: Literal["draft"]
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
    lines: list[SalesOrderLineResponse]


class SalesOrderSearchItem(BaseModel):
    sales_order_id: UUID
    status: Literal["draft"]
    version: int
    branch_id: UUID
    customer_id: UUID
    customer_name: str
    currency: str
    grand_total: Decimal
    payment_timing_policy: PaymentTimingPolicy


class SalesOrderSearchResponse(BaseModel):
    items: list[SalesOrderSearchItem]
    total: int


class OrderEntryAddressResponse(BaseModel):
    address_version_id: UUID
    address_key: str
    version: int
    line_1: str
    line_2: str | None
    city: str
    region: str
    postal_code: str
    country_code: str


class OrderEntryItemResponse(BaseModel):
    price_list_line_id: UUID
    sku_id: UUID
    sku_code: str
    sku_name: str
    unit_code: str
    base_stocking_unit: str
    base_quantity_per_unit: Decimal
    unit_conversion_id: UUID | None
    unit_conversion_version: int | None
    list_unit_price: Decimal
    floor_unit_price: Decimal | None
    tax_code: str
    tax_code_version_id: UUID
    tax_rate: Decimal


class OrderEntryReferenceResponse(BaseModel):
    branch_id: UUID
    customer_id: UUID
    customer_name: str
    customer_version: int
    payment_timing_default: PaymentTimingPolicy
    currency: str
    pricing_date: date
    price_list_version_id: UUID
    price_list_code: str
    price_list_version: int
    price_inclusion_mode: PriceInclusionMode
    addresses: list[OrderEntryAddressResponse]
    items: list[OrderEntryItemResponse]


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
    response: SalesOrderLineResponse
    database_values: dict[str, object]


@dataclass(frozen=True, slots=True)
class LineCalculationInput:
    command: SalesOrderLineInput
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


def command_hash(operation: str, command: BaseModel, context: str = "") -> str:
    payload = f"{operation}:{context}:{command.model_dump_json(exclude_none=False)}"
    return sha256(payload.encode()).hexdigest()


def money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(currency_quantum(currency), ROUND_HALF_UP)


@router.post(
    "/tax-code-versions",
    response_model=TaxCodeVersionResponse,
    status_code=201,
    responses=error_responses(401, 403, 409, 422, 500),
)
async def create_tax_code_version(
    command: CreateTaxCodeVersionCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_sales_pricing_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> TaxCodeVersionResponse:
    request_hash = command_hash("create_tax_code_version", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        return TaxCodeVersionResponse.model_validate(replay)

    tax_code = (
        (await session.execute(select(tax_codes).where(tax_codes.c.code == command.code)))
        .mappings()
        .one_or_none()
    )
    tax_code_id = tax_code["tax_code_id"] if tax_code else uuid4()
    if tax_code is not None and tax_code["name"] != command.name:
        raise AppError(409, "tax_code_conflict", "The Tax Code has a different name.")
    next_version = (
        await session.scalar(
            select(func.coalesce(func.max(tax_code_versions.c.version), 0) + 1).where(
                tax_code_versions.c.tax_code_id == tax_code_id
            )
        )
        or 1
    )
    tax_code_version_id = uuid4()
    result = TaxCodeVersionResponse(
        tax_code_id=tax_code_id,
        tax_code_version_id=tax_code_version_id,
        code=command.code,
        name=command.name,
        version=next_version,
        rate=command.rate,
        effective_from=command.effective_from,
        effective_to=command.effective_to,
    )
    try:
        if tax_code is None:
            await session.execute(
                insert(tax_codes).values(
                    tax_code_id=tax_code_id,
                    code=command.code,
                    name=command.name,
                    created_by=actor.subject,
                )
            )
        await session.execute(
            insert(tax_code_versions).values(
                tax_code_version_id=tax_code_version_id,
                tax_code_id=tax_code_id,
                version=next_version,
                rate=command.rate,
                effective_from=command.effective_from,
                effective_to=command.effective_to,
                created_by=actor.subject,
            )
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
            "tax_code_version_conflict",
            "The Tax Code version conflicts with an existing effective period.",
        ) from error


async def validate_price_list_item(
    session: AsyncSession,
    item: PriceListLineInput,
    effective_from: date,
    effective_to: date | None,
) -> None:
    sku = (
        (await session.execute(select(skus).where(skus.c.sku_id == item.sku_id)))
        .mappings()
        .one_or_none()
    )
    if sku is None or not sku["is_active"]:
        raise AppError(404, "sku_not_found", "The active SKU does not exist.")
    if item.unit_code != sku["base_stocking_unit"]:
        conversion = await session.scalar(
            select(unit_conversions.c.unit_conversion_id).where(
                unit_conversions.c.sku_id == item.sku_id,
                unit_conversions.c.unit_code == item.unit_code,
                unit_conversions.c.effective_from <= effective_from,
                or_(
                    unit_conversions.c.effective_to.is_(None),
                    unit_conversions.c.effective_to >= (effective_to or date.max),
                ),
            )
        )
        if conversion is None:
            raise AppError(
                422,
                "unit_conversion_not_effective",
                "The selling unit lacks a conversion covering the Price List period.",
            )
    tax_version = (
        (
            await session.execute(
                select(tax_code_versions).where(
                    tax_code_versions.c.tax_code_version_id == item.tax_code_version_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if tax_version is None:
        raise AppError(404, "tax_code_version_not_found", "The Tax Code version does not exist.")
    if tax_version["effective_from"] > effective_from or (
        tax_version["effective_to"] is not None
        and (effective_to is None or tax_version["effective_to"] < effective_to)
    ):
        raise AppError(
            422,
            "tax_code_not_effective",
            "The Tax Code version does not cover the Price List period.",
        )


@router.post(
    "/price-list-versions",
    response_model=PriceListVersionResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def create_price_list_version(
    command: CreatePriceListVersionCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_sales_pricing_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> PriceListVersionResponse:
    if command.branch_id not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    if command.customer_id is not None:
        customer_branch = await session.scalar(
            select(customer_accounts.c.branch_id).where(
                customer_accounts.c.customer_id == command.customer_id
            )
        )
        if customer_branch is None:
            raise AppError(404, "customer_not_found", "The Customer Account does not exist.")
        if customer_branch != command.branch_id:
            raise AppError(
                409,
                "customer_branch_conflict",
                "The Customer Account does not belong to the Price List Branch.",
            )
    request_hash = command_hash("create_price_list_version", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        return PriceListVersionResponse.model_validate(replay)

    currency = await session.scalar(select(companies.c.base_currency))
    if currency is None:
        raise AppError(409, "base_currency_not_configured", "Base Currency is not configured.")
    for item in command.items:
        await validate_price_list_item(
            session,
            item,
            command.effective_from,
            command.effective_to,
        )
    existing = (
        (await session.execute(select(price_lists).where(price_lists.c.code == command.code)))
        .mappings()
        .one_or_none()
    )
    if existing is not None and (
        existing["branch_id"] != command.branch_id or existing["customer_id"] != command.customer_id
    ):
        raise AppError(409, "price_list_conflict", "The Price List code has another assignment.")
    price_list_id = existing["price_list_id"] if existing else uuid4()
    next_version = (
        await session.scalar(
            select(func.coalesce(func.max(price_list_versions.c.version), 0) + 1).where(
                price_list_versions.c.price_list_id == price_list_id
            )
        )
        or 1
    )
    price_list_version_id = uuid4()
    item_results = [
        PriceListLineResponse(
            price_list_line_id=uuid4(),
            **item.model_dump(),
        )
        for item in command.items
    ]
    result = PriceListVersionResponse(
        price_list_id=price_list_id,
        price_list_version_id=price_list_version_id,
        code=command.code,
        version=next_version,
        branch_id=command.branch_id,
        customer_id=command.customer_id,
        currency=currency,
        inclusion_mode=command.inclusion_mode,
        effective_from=command.effective_from,
        effective_to=command.effective_to,
        items=item_results,
    )
    try:
        if existing is None:
            await session.execute(
                insert(price_lists).values(
                    price_list_id=price_list_id,
                    branch_id=command.branch_id,
                    customer_id=command.customer_id,
                    code=command.code,
                    created_by=actor.subject,
                )
            )
        await session.execute(
            insert(price_list_versions).values(
                price_list_version_id=price_list_version_id,
                price_list_id=price_list_id,
                version=next_version,
                currency=currency,
                inclusion_mode=command.inclusion_mode,
                effective_from=command.effective_from,
                effective_to=command.effective_to,
                created_by=actor.subject,
            )
        )
        for position, result_item in enumerate(item_results, start=1):
            await session.execute(
                insert(price_list_lines).values(
                    price_list_line_id=result_item.price_list_line_id,
                    price_list_version_id=price_list_version_id,
                    sku_id=result_item.sku_id,
                    unit_code=result_item.unit_code,
                    list_unit_price=result_item.list_unit_price,
                    floor_unit_price=result_item.floor_unit_price,
                    tax_code_version_id=result_item.tax_code_version_id,
                    line_position=position,
                )
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
            "price_list_version_conflict",
            "The Price List conflicts with an effective assignment or identifier.",
        ) from error


async def load_draft_context(
    session: AsyncSession,
    command: SalesOrderDraftFields,
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
            "The Customer Account does not belong to the Sales Order Branch.",
        )
    if customer["version"] != command.expected_customer_version:
        raise AppError(
            409,
            "reference_data_conflict",
            "The Customer Account changed after order-entry references were loaded.",
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
            "A new draft revision requires a current delivery address.",
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


async def effective_price_list(
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


@router.get(
    "/order-entry-reference",
    response_model=OrderEntryReferenceResponse,
    responses=error_responses(401, 403, 404, 422, 500),
)
async def get_order_entry_reference(
    branch_id: UUID,
    customer_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_sales_order_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> OrderEntryReferenceResponse:
    if branch_id not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    customer = (
        (
            await session.execute(
                select(customer_accounts).where(customer_accounts.c.customer_id == customer_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if customer is None:
        raise AppError(404, "customer_not_found", "The Customer Account does not exist.")
    if customer["branch_id"] != branch_id:
        raise AppError(
            409,
            "customer_branch_conflict",
            "The Customer Account does not belong to the requested Branch.",
        )
    if customer["status"] != "active":
        raise AppError(409, "customer_inactive", "The Customer Account is not active.")
    pricing_date = date.today()
    price_list = await effective_price_list(
        session,
        branch_id=branch_id,
        customer_id=customer_id,
        pricing_date=pricing_date,
    )
    address_rows = (
        (
            await session.execute(
                select(customer_address_versions)
                .where(
                    customer_address_versions.c.customer_id == customer_id,
                    customer_address_versions.c.kind == "delivery",
                    customer_address_versions.c.is_current.is_(True),
                )
                .order_by(customer_address_versions.c.address_key)
            )
        )
        .mappings()
        .all()
    )
    item_rows = (
        (
            await session.execute(
                select(
                    price_list_lines,
                    skus.c.code.label("sku_code"),
                    skus.c.name.label("sku_name"),
                    skus.c.base_stocking_unit,
                    tax_codes.c.code.label("tax_code"),
                    tax_code_versions.c.rate.label("tax_rate"),
                )
                .select_from(
                    price_list_lines.join(
                        skus,
                        price_list_lines.c.sku_id == skus.c.sku_id,
                    )
                    .join(
                        tax_code_versions,
                        price_list_lines.c.tax_code_version_id
                        == tax_code_versions.c.tax_code_version_id,
                    )
                    .join(
                        tax_codes,
                        tax_code_versions.c.tax_code_id == tax_codes.c.tax_code_id,
                    )
                )
                .where(
                    price_list_lines.c.price_list_version_id == price_list["price_list_version_id"]
                )
                .order_by(price_list_lines.c.line_position)
            )
        )
        .mappings()
        .all()
    )
    items: list[OrderEntryItemResponse] = []
    for item in item_rows:
        factor = Decimal("1")
        conversion_id: UUID | None = None
        conversion_version: int | None = None
        if item["unit_code"] != item["base_stocking_unit"]:
            conversion = (
                (
                    await session.execute(
                        select(unit_conversions).where(
                            unit_conversions.c.sku_id == item["sku_id"],
                            unit_conversions.c.unit_code == item["unit_code"],
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
                    409,
                    "reference_data_conflict",
                    "The Price List references an unavailable Unit Conversion.",
                )
            factor = conversion["base_quantity"]
            conversion_id = conversion["unit_conversion_id"]
            conversion_version = conversion["version"]
        items.append(
            OrderEntryItemResponse(
                price_list_line_id=item["price_list_line_id"],
                sku_id=item["sku_id"],
                sku_code=item["sku_code"],
                sku_name=item["sku_name"],
                unit_code=item["unit_code"],
                base_stocking_unit=item["base_stocking_unit"],
                base_quantity_per_unit=factor,
                unit_conversion_id=conversion_id,
                unit_conversion_version=conversion_version,
                list_unit_price=item["list_unit_price"],
                floor_unit_price=item["floor_unit_price"],
                tax_code=item["tax_code"],
                tax_code_version_id=item["tax_code_version_id"],
                tax_rate=item["tax_rate"],
            )
        )
    return OrderEntryReferenceResponse(
        branch_id=branch_id,
        customer_id=customer_id,
        customer_name=customer["legal_name"],
        customer_version=customer["version"],
        payment_timing_default=customer["payment_timing_policy"],
        currency=price_list["currency"],
        pricing_date=pricing_date,
        price_list_version_id=price_list["price_list_version_id"],
        price_list_code=price_list["code"],
        price_list_version=price_list["version"],
        price_inclusion_mode=price_list["inclusion_mode"],
        addresses=[OrderEntryAddressResponse.model_validate(row) for row in address_rows],
        items=items,
    )


async def calculate_draft(
    session: AsyncSession,
    *,
    command: SalesOrderDraftFields,
    context: DraftContext,
    actor: AuthorizedUser,
    sales_order_id: UUID,
    version: int,
    idempotency_key: str,
    correlation_id: str,
) -> tuple[SalesOrderDraftResponse, UUID, list[CalculatedLine], dict[str, object]]:
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
            "The pricing date changed after order-entry references were loaded.",
        )
    price_list = await effective_price_list(
        session,
        branch_id=command.branch_id,
        customer_id=command.customer_id,
        pricing_date=pricing_date,
    )
    if price_list["price_list_version_id"] != command.expected_price_list_version_id:
        raise AppError(
            409,
            "reference_data_conflict",
            "The effective Price List changed after order-entry references were loaded.",
        )
    if price_list["currency"] != context.currency:
        raise AppError(
            409,
            "price_list_currency_conflict",
            "Sales Price Lists must use the Company Base Currency.",
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
                "A Price List line changed after order-entry references were loaded.",
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
                    "No effective Unit Conversion exists for the Sales Order date.",
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
                "A Unit Conversion changed after order-entry references were loaded.",
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
        pre_discount = money(extended_price, context.currency)
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
    discount_total = money(command.order_discount_amount, context.currency)
    if discount_total > subtotal:
        raise AppError(
            422,
            "discount_exceeds_value",
            "The order discount cannot exceed the pre-discount value.",
        )
    allocations = allocate_largest_remainder(
        amount=discount_total,
        weighted_lines=[
            (item.position, item.command.line_id, item.pre_discount) for item in line_inputs
        ],
        quantum=quantum,
    )
    calculated_lines: list[CalculatedLine] = []
    response_lines: list[SalesOrderLineResponse] = []
    for item in line_inputs:
        line = item.command
        price = item.price
        allocated = allocations[line.line_id]
        after_discount = money(item.pre_discount - allocated, context.currency)
        tax_rate = price["tax_rate"]
        if price_list["inclusion_mode"] == "exclusive":
            taxable = after_discount
            tax_amount = money(taxable * tax_rate, context.currency)
            line_total = money(taxable + tax_amount, context.currency)
        else:
            line_total = after_discount
            taxable = money(line_total / (Decimal("1") + tax_rate), context.currency)
            tax_amount = money(line_total - taxable, context.currency)
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
        response_line = SalesOrderLineResponse(
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
                    "sales_order_line_revision_id": uuid4(),
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
        "sales_order_revision_id": revision_id,
        "sales_order_id": sales_order_id,
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
        "calculation_contract_version": 1,
        "actor_subject": actor.subject,
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
    }
    result = SalesOrderDraftResponse(
        sales_order_id=sales_order_id,
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
        lines=response_lines,
    )
    return result, revision_id, calculated_lines, revision_values


async def persist_revision(
    session: AsyncSession,
    *,
    revision_id: UUID,
    revision_values: dict[str, object],
    calculated_lines: list[CalculatedLine],
) -> None:
    await session.execute(insert(sales_order_revisions).values(**revision_values))
    for line in calculated_lines:
        await session.execute(
            insert(sales_order_line_revisions).values(
                sales_order_revision_id=revision_id,
                **line.database_values,
            )
        )


@router.post(
    "/orders",
    response_model=SalesOrderDraftResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def create_sales_order_draft(
    command: CreateSalesOrderDraftCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_sales_order_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> SalesOrderDraftResponse:
    if command.branch_id not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    request_hash = command_hash("create_sales_order_draft", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        return SalesOrderDraftResponse.model_validate(replay)
    context = await load_draft_context(session, command, actor)
    result, revision_id, lines, revision_values = await calculate_draft(
        session,
        command=command,
        context=context,
        actor=actor,
        sales_order_id=command.sales_order_id,
        version=1,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    try:
        await session.execute(
            insert(sales_orders).values(
                sales_order_id=command.sales_order_id,
                branch_id=command.branch_id,
                customer_id=command.customer_id,
                created_by=actor.subject,
                updated_by=actor.subject,
            )
        )
        await persist_revision(
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
            "sales_order_draft_conflict",
            "The Sales Order Draft identity or revision already exists.",
        ) from error


@router.put(
    "/orders/{sales_order_id}",
    response_model=SalesOrderDraftResponse,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def update_sales_order_draft(
    sales_order_id: UUID,
    command: UpdateSalesOrderDraftCommand,
    request: Request,
    actor: Annotated[AuthorizedUser, Depends(require_sales_order_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> SalesOrderDraftResponse:
    existing = (
        (
            await session.execute(
                select(sales_orders).where(sales_orders.c.sales_order_id == sales_order_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is None:
        raise AppError(404, "sales_order_not_found", "The Sales Order Draft does not exist.")
    if existing["branch_id"] not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    request_hash = command_hash(
        "update_sales_order_draft",
        command,
        context=f"{sales_order_id}:{if_match}",
    )
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return SalesOrderDraftResponse.model_validate(replay)
    context = await load_draft_context(session, command, actor)
    locked = (
        (
            await session.execute(
                select(sales_orders)
                .where(sales_orders.c.sales_order_id == sales_order_id)
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
            "The Sales Order Draft has changed and requires explicit review.",
        )
    next_version = if_match + 1
    result, revision_id, lines, revision_values = await calculate_draft(
        session,
        command=command,
        context=context,
        actor=actor,
        sales_order_id=sales_order_id,
        version=next_version,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    try:
        updated = await session.execute(
            update(sales_orders)
            .where(
                sales_orders.c.sales_order_id == sales_order_id,
                sales_orders.c.version == if_match,
            )
            .values(
                branch_id=command.branch_id,
                customer_id=command.customer_id,
                version=next_version,
                updated_by=actor.subject,
                updated_at=func.now(),
            )
            .returning(sales_orders.c.sales_order_id)
        )
        if updated.scalar_one_or_none() is None:
            raise AppError(
                409,
                "optimistic_version_conflict",
                "The Sales Order Draft has changed and requires explicit review.",
            )
        await persist_revision(
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
            "sales_order_revision_conflict",
            "The Sales Order Draft revision conflicts with current server state.",
        ) from error


async def load_sales_order_response(
    session: AsyncSession,
    sales_order_id: UUID,
    actor: AuthorizedUser,
) -> SalesOrderDraftResponse:
    order = (
        (
            await session.execute(
                select(sales_orders).where(sales_orders.c.sales_order_id == sales_order_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if order is None:
        raise AppError(404, "sales_order_not_found", "The Sales Order Draft does not exist.")
    if order["branch_id"] not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    revision = (
        (
            await session.execute(
                select(sales_order_revisions).where(
                    sales_order_revisions.c.sales_order_id == sales_order_id,
                    sales_order_revisions.c.version == order["version"],
                )
            )
        )
        .mappings()
        .one()
    )
    rows = (
        (
            await session.execute(
                select(sales_order_line_revisions)
                .where(
                    sales_order_line_revisions.c.sales_order_revision_id
                    == revision["sales_order_revision_id"]
                )
                .order_by(sales_order_line_revisions.c.line_position)
            )
        )
        .mappings()
        .all()
    )
    currency = revision["currency"]
    lines = [
        SalesOrderLineResponse(
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
            allocated_discount=money(row["allocated_discount"], currency),
            tax_snapshot=row["tax_snapshot"],
            calculation_snapshot=row["calculation_snapshot"],
            taxable_amount=money(row["taxable_amount"], currency),
            tax_amount=money(row["tax_amount"], currency),
            line_total=money(row["line_total"], currency),
            below_floor=row["below_floor"],
        )
        for row in rows
    ]
    return SalesOrderDraftResponse(
        sales_order_id=sales_order_id,
        status="draft",
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
        payment_timing_default=revision["payment_timing_default"],
        payment_timing_policy=revision["payment_timing_policy"],
        payment_timing_override_reason=revision["payment_timing_override_reason"],
        payment_timing_overridden_by=revision["payment_timing_overridden_by"],
        order_discount_amount=revision["order_discount_amount"],
        subtotal=money(revision["subtotal"], currency),
        discount_total=money(revision["discount_total"], currency),
        taxable_total=money(revision["taxable_total"], currency),
        tax_total=money(revision["tax_total"], currency),
        grand_total=money(revision["grand_total"], currency),
        calculation_contract_version=revision["calculation_contract_version"],
        lines=lines,
    )


@router.get(
    "/orders/{sales_order_id}",
    response_model=SalesOrderDraftResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_sales_order_draft(
    sales_order_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_sales_order_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> SalesOrderDraftResponse:
    return await load_sales_order_response(session, sales_order_id, actor)


@router.get(
    "/orders",
    response_model=SalesOrderSearchResponse,
    responses=error_responses(401, 403, 422, 500),
)
async def search_sales_order_drafts(
    actor: Annotated[AuthorizedUser, Depends(require_sales_order_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    query: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SalesOrderSearchResponse:
    if not actor.branch_ids:
        return SalesOrderSearchResponse(items=[], total=0)
    latest = sales_orders.join(
        sales_order_revisions,
        (sales_orders.c.sales_order_id == sales_order_revisions.c.sales_order_id)
        & (sales_orders.c.version == sales_order_revisions.c.version),
    ).join(
        customer_accounts,
        sales_orders.c.customer_id == customer_accounts.c.customer_id,
    )
    filters: list[ColumnElement[bool]] = [sales_orders.c.branch_id.in_(actor.branch_ids)]
    if query.strip():
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                customer_accounts.c.legal_name.ilike(pattern),
                customer_accounts.c.account_number.ilike(pattern),
            )
        )
    total = await session.scalar(select(func.count()).select_from(latest).where(*filters))
    rows = (
        await session.execute(
            select(
                sales_orders.c.sales_order_id,
                sales_orders.c.status,
                sales_orders.c.version,
                sales_orders.c.branch_id,
                sales_orders.c.customer_id,
                customer_accounts.c.legal_name.label("customer_name"),
                sales_order_revisions.c.currency,
                sales_order_revisions.c.grand_total,
                sales_order_revisions.c.payment_timing_policy,
            )
            .select_from(latest)
            .where(*filters)
            .order_by(sales_orders.c.updated_at.desc(), sales_orders.c.sales_order_id)
            .limit(limit)
        )
    ).mappings()
    return SalesOrderSearchResponse(
        items=[
            SalesOrderSearchItem(
                **row,
                grand_total=money(row["grand_total"], row["currency"]),
            )
            for row in rows
        ],
        total=total or 0,
    )
