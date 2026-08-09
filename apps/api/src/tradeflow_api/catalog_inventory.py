from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import delete, exists, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tradeflow_api.auth import (
    AuthorizedUser,
    require_catalog_writer,
    require_inventory_poster,
    require_inventory_reader,
    require_inventory_rebuilder,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    barcode_mappings,
    companies,
    delivery_confirmation_lines,
    delivery_line_identity_allocations,
    delivery_lines,
    inventory_availability,
    inventory_reserved_by_sku_warehouse,
    inventory_valuation,
    lot_identities,
    pick_identity_assignments,
    pick_lines,
    products,
    skus,
    stock_lot_allocations,
    stock_movement_identity_allocations,
    stock_movements,
    stock_serial_allocations,
    unit_conversions,
    warehouse_stock_locations,
    warehouses,
)
from tradeflow_api.money import currency_quantum

router = APIRouter(tags=["catalog and inventory"])
SIX_PLACES = Decimal("0.000001")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversionInput(CommandModel):
    unit_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")
    base_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def valid_range(self) -> ConversionInput:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("Unit Conversion effective_to must not precede effective_from.")
        return self


class BarcodeInput(CommandModel):
    barcode: str = Field(min_length=1, max_length=100)
    unit_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")


class ConfigureSkuCommand(CommandModel):
    product_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{1,49}$")
    product_name: str = Field(min_length=1, max_length=200)
    sku_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{1,49}$")
    sku_name: str = Field(min_length=1, max_length=200)
    base_stocking_unit: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")
    tracking_policy: Literal["untracked", "lot", "serial"]
    expiration_control: bool = False
    conversions: list[ConversionInput] = Field(default_factory=list)
    barcodes: list[BarcodeInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mappings(self) -> ConfigureSkuCommand:
        if self.expiration_control and self.tracking_policy == "untracked":
            raise ValueError("Expiration Control requires Lot or Serial tracking.")
        conversion_units = [conversion.unit_code for conversion in self.conversions]
        if self.base_stocking_unit in conversion_units:
            raise ValueError("Base Stocking Unit must not be configured as a conversion.")
        for unit_code in set(conversion_units):
            periods = sorted(
                (
                    conversion.effective_from,
                    conversion.effective_to or date.max,
                )
                for conversion in self.conversions
                if conversion.unit_code == unit_code
            )
            if any(
                current_start <= previous_end
                for (_, previous_end), (current_start, _) in zip(periods, periods[1:], strict=False)
            ):
                raise ValueError("Effective Unit Conversion periods must not overlap.")
        barcodes = [mapping.barcode for mapping in self.barcodes]
        if len(barcodes) != len(set(barcodes)):
            raise ValueError("Barcode mappings must be unique in this command.")
        valid_units = {self.base_stocking_unit, *conversion_units}
        if any(
            mapping.unit_code is not None and mapping.unit_code not in valid_units
            for mapping in self.barcodes
        ):
            raise ValueError("Barcode mapping references an unknown unit.")
        return self


class ConversionResponse(BaseModel):
    unit_conversion_id: UUID
    unit_code: str
    base_quantity: Decimal
    effective_from: date
    effective_to: date | None
    version: int


class BarcodeResponse(BaseModel):
    barcode_mapping_id: UUID
    barcode: str
    unit_code: str


class SkuResponse(BaseModel):
    product_id: UUID
    product_code: str
    product_name: str
    sku_id: UUID
    sku_code: str
    sku_name: str
    base_stocking_unit: str
    tracking_policy: Literal["untracked", "lot", "serial"]
    expiration_control: bool
    version: int
    conversions: list[ConversionResponse]
    barcodes: list[BarcodeResponse]


class UpdateConversionCommand(CommandModel):
    base_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def valid_range(self) -> UpdateConversionCommand:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("Unit Conversion effective_to must not precede effective_from.")
        return self


class CreateLocationCommand(CommandModel):
    warehouse_id: UUID
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{1,49}$")
    name: str = Field(min_length=1, max_length=200)
    custody: Literal["available", "quarantine"]


class LocationResponse(BaseModel):
    location_id: UUID
    warehouse_id: UUID
    code: str
    name: str
    custody: Literal["available", "quarantine", "dispatch_staging", "in_transit"]
    version: int


class OpeningStockCommand(CommandModel):
    sku_id: UUID
    warehouse_id: UUID
    location_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    source_reference: str = Field(min_length=1, max_length=100)
    lot_code: str | None = Field(default=None, max_length=100)
    serial_numbers: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    ] = Field(default_factory=list)
    expiration_date: date | None = None


class OpeningStockResponse(BaseModel):
    movement_id: UUID
    sku_id: UUID
    warehouse_id: UUID
    location_id: UUID
    quantity_base: Decimal
    value_delta: Decimal
    moving_average_unit_cost: Decimal
    base_currency: str
    source_reference: str
    entered_unit: str
    conversion_snapshot: dict[str, str]


class AvailabilityItem(BaseModel):
    sku_id: UUID
    sku_code: str
    sku_name: str
    warehouse_id: UUID
    warehouse_code: str
    location_code: str
    custody: Literal["available", "quarantine", "dispatch_staging", "in_transit"]
    base_stocking_unit: str
    tracking_policy: Literal["untracked", "lot", "serial"]
    expiration_control: bool
    base_currency: str
    lot_code: str | None
    serial_numbers: list[str]
    expiration_date: date | None
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    commercial_reserved: Decimal
    warehouse_on_hand: Decimal
    warehouse_available: Decimal
    warehouse_inventory_value: Decimal
    moving_average_unit_cost: Decimal


class AvailabilityResponse(BaseModel):
    items: list[AvailabilityItem]
    total: int


class ProjectionRebuildResponse(BaseModel):
    availability_rows: int
    valuation_rows: int


def command_hash(operation: str, command: BaseModel, context: str = "") -> str:
    payload = f"{operation}:{context}:{command.model_dump_json(exclude_none=False)}"
    return sha256(payload.encode()).hexdigest()


async def conversion_response_rows(session: AsyncSession, sku_id: UUID) -> list[ConversionResponse]:
    rows = (
        await session.execute(
            select(unit_conversions)
            .where(unit_conversions.c.sku_id == sku_id)
            .order_by(
                unit_conversions.c.unit_code,
                unit_conversions.c.effective_from,
            )
        )
    ).mappings()
    return [ConversionResponse.model_validate(row) for row in rows]


@router.post(
    "/v1/catalog/skus",
    response_model=SkuResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
async def configure_sku(
    command: ConfigureSkuCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_catalog_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> SkuResponse:
    request_hash = command_hash("configure_sku", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        return SkuResponse.model_validate(replay)

    product_id = uuid4()
    sku_id = uuid4()
    conversion_ids: dict[str, UUID] = {}
    try:
        existing_product = (
            (await session.execute(select(products).where(products.c.code == command.product_code)))
            .mappings()
            .one_or_none()
        )
        if existing_product is None:
            await session.execute(
                insert(products).values(
                    product_id=product_id,
                    code=command.product_code,
                    name=command.product_name,
                    created_by=actor.subject,
                )
            )
        else:
            if existing_product["name"] != command.product_name:
                raise AppError(
                    409,
                    "product_definition_conflict",
                    "The Product code already identifies a different Product.",
                )
            product_id = existing_product["product_id"]
        await session.execute(
            insert(skus).values(
                sku_id=sku_id,
                product_id=product_id,
                code=command.sku_code,
                name=command.sku_name,
                base_stocking_unit=command.base_stocking_unit,
                tracking_policy=command.tracking_policy,
                expiration_control=command.expiration_control,
                created_by=actor.subject,
            )
        )
        conversion_results: list[ConversionResponse] = []
        for conversion in command.conversions:
            conversion_id = uuid4()
            conversion_ids[conversion.unit_code] = conversion_id
            await session.execute(
                insert(unit_conversions).values(
                    unit_conversion_id=conversion_id,
                    sku_id=sku_id,
                    created_by=actor.subject,
                    **conversion.model_dump(),
                )
            )
            conversion_results.append(
                ConversionResponse(
                    unit_conversion_id=conversion_id,
                    version=1,
                    **conversion.model_dump(),
                )
            )
        barcode_results: list[BarcodeResponse] = []
        for mapping in command.barcodes:
            barcode_id = uuid4()
            unit_code = mapping.unit_code or command.base_stocking_unit
            await session.execute(
                insert(barcode_mappings).values(
                    barcode_mapping_id=barcode_id,
                    sku_id=sku_id,
                    unit_conversion_id=conversion_ids.get(unit_code),
                    barcode=mapping.barcode,
                    created_by=actor.subject,
                )
            )
            barcode_results.append(
                BarcodeResponse(
                    barcode_mapping_id=barcode_id,
                    barcode=mapping.barcode,
                    unit_code=unit_code,
                )
            )
        result = SkuResponse(
            product_id=product_id,
            product_code=command.product_code,
            product_name=command.product_name,
            sku_id=sku_id,
            sku_code=command.sku_code,
            sku_name=command.sku_name,
            base_stocking_unit=command.base_stocking_unit,
            tracking_policy=command.tracking_policy,
            expiration_control=command.expiration_control,
            version=1,
            conversions=conversion_results,
            barcodes=barcode_results,
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
        constraint = str(error.orig)
        if "ex_unit_conversion_effective_period" in constraint:
            raise AppError(
                409,
                "unit_conversion_conflict",
                "A Unit Conversion effective period overlaps an existing period.",
            ) from error
        code = (
            "active_barcode_exists"
            if "uq_active_barcode" in constraint
            else "catalog_identifier_exists"
        )
        raise AppError(
            409, code, "An active Barcode, Product, or SKU identifier already exists."
        ) from error


@router.put(
    "/v1/catalog/skus/{sku_id}/unit-conversions/{conversion_id}",
    response_model=ConversionResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def update_unit_conversion(
    sku_id: UUID,
    conversion_id: UUID,
    command: UpdateConversionCommand,
    actor: Annotated[AuthorizedUser, Depends(require_catalog_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> ConversionResponse:
    request_hash = command_hash(
        "update_unit_conversion",
        command,
        context=f"{sku_id}:{conversion_id}:{if_match}",
    )
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return ConversionResponse.model_validate(replay)
    row = (
        (
            await session.execute(
                select(unit_conversions)
                .where(
                    unit_conversions.c.unit_conversion_id == conversion_id,
                    unit_conversions.c.sku_id == sku_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "unit_conversion_not_found", "The Unit Conversion does not exist.")
    if row["version"] != if_match:
        raise AppError(409, "optimistic_version_conflict", "The Unit Conversion has changed.")
    used = await session.scalar(
        select(func.count())
        .select_from(stock_movements)
        .where(
            stock_movements.c.sku_id == sku_id,
            stock_movements.c.conversion_snapshot["unit_conversion_id"].astext
            == str(conversion_id),
        )
    )
    if used:
        raise AppError(
            409,
            "unit_conversion_in_use",
            "A Unit Conversion used by a posted movement is immutable.",
        )
    result = ConversionResponse(
        unit_conversion_id=conversion_id,
        unit_code=row["unit_code"],
        version=if_match + 1,
        **command.model_dump(),
    )
    try:
        updated = await session.execute(
            update(unit_conversions)
            .where(
                unit_conversions.c.unit_conversion_id == conversion_id,
                unit_conversions.c.sku_id == sku_id,
                unit_conversions.c.version == if_match,
            )
            .values(**command.model_dump(), version=if_match + 1)
            .returning(unit_conversions.c.unit_conversion_id)
        )
        if updated.scalar_one_or_none() is None:
            raise AppError(409, "optimistic_version_conflict", "The Unit Conversion has changed.")
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
            "unit_conversion_conflict",
            "The Unit Conversion conflicts with another effective period.",
        ) from error


@router.post(
    "/v1/inventory/locations",
    response_model=LocationResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def create_stock_location(
    command: CreateLocationCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_catalog_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> LocationResponse:
    if command.warehouse_id not in actor.warehouse_ids:
        raise AppError(403, "operational_scope_required", "Warehouse scope is required.")
    warehouse_active = await session.scalar(
        select(warehouses.c.is_active).where(warehouses.c.warehouse_id == command.warehouse_id)
    )
    if warehouse_active is None:
        raise AppError(404, "warehouse_not_found", "The Warehouse does not exist.")
    if not warehouse_active:
        raise AppError(409, "warehouse_inactive", "The Warehouse is inactive.")
    request_hash = command_hash("create_stock_location", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        return LocationResponse.model_validate(replay)
    location_id = uuid4()
    result = LocationResponse(location_id=location_id, version=1, **command.model_dump())
    try:
        await session.execute(
            insert(warehouse_stock_locations).values(
                location_id=location_id,
                created_by=actor.subject,
                **command.model_dump(),
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
    except IntegrityError as error:
        await session.rollback()
        raise AppError(
            409, "stock_location_exists", "The Stock Location code already exists."
        ) from error
    return result


async def load_opening_context(
    session: AsyncSession,
    command: OpeningStockCommand,
    actor: AuthorizedUser,
) -> tuple[RowMapping, RowMapping]:
    sku = (
        (await session.execute(select(skus).where(skus.c.sku_id == command.sku_id)))
        .mappings()
        .one_or_none()
    )
    if sku is None:
        raise AppError(404, "sku_not_found", "The SKU does not exist.")
    if not sku["is_active"]:
        raise AppError(409, "sku_inactive", "The SKU is inactive.")
    if command.warehouse_id not in actor.warehouse_ids:
        raise AppError(403, "operational_scope_required", "Warehouse scope is required.")
    location = (
        (
            await session.execute(
                select(
                    warehouse_stock_locations,
                    warehouses.c.is_active.label("warehouse_active"),
                )
                .select_from(
                    warehouse_stock_locations.join(
                        warehouses,
                        warehouse_stock_locations.c.warehouse_id == warehouses.c.warehouse_id,
                    )
                )
                .where(warehouse_stock_locations.c.location_id == command.location_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if location is None or location["warehouse_id"] != command.warehouse_id:
        raise AppError(404, "stock_location_not_found", "The Stock Location does not exist.")
    if not location["is_active"] or not location["warehouse_active"]:
        raise AppError(
            409, "stock_location_inactive", "The Warehouse or Stock Location is inactive."
        )
    return sku, location


def validate_tracking(
    command: OpeningStockCommand,
    *,
    tracking_policy: str,
    expiration_control: bool,
    quantity_base: Decimal,
    custody: str,
) -> None:
    if tracking_policy == "untracked":
        if command.lot_code or command.serial_numbers or command.expiration_date:
            raise AppError(
                422, "tracking_not_allowed", "Untracked stock cannot include identities."
            )
    elif tracking_policy == "lot":
        if not command.lot_code or command.serial_numbers:
            raise AppError(
                422, "lot_identity_required", "Lot-tracked stock requires one Lot Identity."
            )
    else:
        if command.lot_code or not command.serial_numbers:
            raise AppError(
                422,
                "serial_identities_required",
                "Serial-tracked stock requires Serial Identities.",
            )
        if quantity_base != quantity_base.to_integral_value() or len(command.serial_numbers) != int(
            quantity_base
        ):
            raise AppError(
                422,
                "serial_quantity_mismatch",
                "Serial Identity count must equal the whole Base Stocking Unit quantity.",
            )
        if len(command.serial_numbers) != len(set(command.serial_numbers)):
            raise AppError(409, "duplicate_serial_identity", "Serial Identities must be unique.")
    if expiration_control and command.expiration_date is None:
        raise AppError(422, "expiration_required", "Expiration-controlled stock requires a date.")
    if (
        custody == "available"
        and command.expiration_date is not None
        and command.expiration_date < date.today()
    ):
        raise AppError(
            409, "expired_stock_not_available", "Expired stock may only enter Quarantine."
        )


@router.post(
    "/v1/inventory/opening-stock",
    response_model=OpeningStockResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def post_opening_stock(
    command: OpeningStockCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_poster)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> OpeningStockResponse:
    sku, location = await load_opening_context(session, command, actor)
    request_hash = command_hash("post_opening_stock", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        return OpeningStockResponse.model_validate(replay)

    factor = Decimal("1")
    conversion_id: UUID | None = None
    if command.unit_code != sku["base_stocking_unit"]:
        conversion = (
            (
                await session.execute(
                    select(unit_conversions)
                    .where(
                        unit_conversions.c.sku_id == command.sku_id,
                        unit_conversions.c.unit_code == command.unit_code,
                        unit_conversions.c.effective_from <= date.today(),
                        or_(
                            unit_conversions.c.effective_to.is_(None),
                            unit_conversions.c.effective_to >= date.today(),
                        ),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if conversion is None:
            raise AppError(
                422,
                "unit_conversion_not_effective",
                "No effective Unit Conversion exists for the entered unit.",
            )
        factor = conversion["base_quantity"]
        conversion_id = conversion["unit_conversion_id"]
    quantity_base = (command.quantity * factor).quantize(SIX_PLACES)
    validate_tracking(
        command,
        tracking_policy=sku["tracking_policy"],
        expiration_control=sku["expiration_control"],
        quantity_base=quantity_base,
        custody=location["custody"],
    )
    base_currency = await session.scalar(select(companies.c.base_currency))
    if base_currency is None:
        raise AppError(
            409,
            "base_currency_not_configured",
            "The Company Base Currency must be configured before posting stock.",
        )
    value_delta = (quantity_base * command.unit_cost).quantize(
        currency_quantum(base_currency), ROUND_HALF_UP
    )
    conversion_snapshot = {
        "entered_quantity": str(command.quantity),
        "entered_unit": command.unit_code,
        "base_quantity_per_unit": str(factor),
        "base_quantity": str(quantity_base),
        "unit_conversion_id": "" if conversion_id is None else str(conversion_id),
    }

    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext('inventory-projection-rebuild'))")
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:stock_key))"),
        {"stock_key": f"{command.sku_id}:{command.warehouse_id}"},
    )
    current = (
        (
            await session.execute(
                select(inventory_valuation).where(
                    inventory_valuation.c.sku_id == command.sku_id,
                    inventory_valuation.c.warehouse_id == command.warehouse_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    next_quantity = quantity_base + (current["quantity_on_hand"] if current else Decimal("0"))
    next_value = value_delta + (current["inventory_value"] if current else Decimal("0"))
    next_average = (next_value / next_quantity).quantize(SIX_PLACES, ROUND_HALF_UP)
    movement_id = uuid4()
    try:
        lot_identity_id: UUID | None = None
        if command.lot_code is not None:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lot_key))"),
                {"lot_key": f"lot:{command.sku_id}:{command.lot_code}"},
            )
            lot_identity = (
                (
                    await session.execute(
                        select(lot_identities)
                        .where(
                            lot_identities.c.sku_id == command.sku_id,
                            lot_identities.c.lot_code == command.lot_code,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if lot_identity is None:
                lot_identity_id = uuid4()
                await session.execute(
                    insert(lot_identities).values(
                        lot_identity_id=lot_identity_id,
                        sku_id=command.sku_id,
                        lot_code=command.lot_code,
                        expiration_date=command.expiration_date,
                    )
                )
            else:
                lot_identity_id = lot_identity["lot_identity_id"]
                if lot_identity["expiration_date"] != command.expiration_date:
                    raise AppError(
                        409,
                        "lot_identity_conflict",
                        "The Lot Identity already has a different expiration date.",
                    )
        await session.execute(
            insert(stock_movements).values(
                movement_id=movement_id,
                sku_id=command.sku_id,
                warehouse_id=command.warehouse_id,
                location_id=command.location_id,
                movement_type="opening_stock",
                quantity_base=quantity_base,
                unit_cost=command.unit_cost,
                value_delta=value_delta,
                base_currency=base_currency,
                source_reference=command.source_reference,
                entered_unit=command.unit_code,
                conversion_snapshot=conversion_snapshot,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                movement_group_id=movement_id,
                movement_leg="opening_in",
            )
        )
        identity_key = (
            f"lot:{command.lot_code}"
            if command.lot_code is not None
            else f"serial:{movement_id}"
            if command.serial_numbers
            else ""
        )
        if command.lot_code is not None:
            await session.execute(
                insert(stock_lot_allocations).values(
                    lot_allocation_id=uuid4(),
                    movement_id=movement_id,
                    lot_identity_id=lot_identity_id,
                    quantity_base=quantity_base,
                )
            )
        for serial_number in command.serial_numbers:
            await session.execute(
                insert(stock_serial_allocations).values(
                    serial_allocation_id=uuid4(),
                    movement_id=movement_id,
                    sku_id=command.sku_id,
                    serial_number=serial_number,
                    expiration_date=command.expiration_date,
                )
            )
        await session.execute(
            pg_insert(inventory_availability)
            .values(
                sku_id=command.sku_id,
                warehouse_id=command.warehouse_id,
                location_id=command.location_id,
                identity_key=identity_key,
                lot_code=command.lot_code,
                serial_numbers=sorted(command.serial_numbers),
                expiration_date=command.expiration_date,
                on_hand=quantity_base,
                reserved=Decimal("0"),
            )
            .on_conflict_do_update(
                index_elements=[
                    "sku_id",
                    "warehouse_id",
                    "location_id",
                    "identity_key",
                ],
                set_={
                    "on_hand": inventory_availability.c.on_hand + quantity_base,
                    "expiration_date": command.expiration_date,
                },
            )
        )
        await session.execute(
            pg_insert(inventory_valuation)
            .values(
                sku_id=command.sku_id,
                warehouse_id=command.warehouse_id,
                quantity_on_hand=next_quantity,
                inventory_value=next_value,
                moving_average_unit_cost=next_average,
            )
            .on_conflict_do_update(
                index_elements=["sku_id", "warehouse_id"],
                set_={
                    "quantity_on_hand": next_quantity,
                    "inventory_value": next_value,
                    "moving_average_unit_cost": next_average,
                },
            )
        )
        result = OpeningStockResponse(
            movement_id=movement_id,
            sku_id=command.sku_id,
            warehouse_id=command.warehouse_id,
            location_id=command.location_id,
            quantity_base=quantity_base,
            value_delta=value_delta,
            moving_average_unit_cost=next_average,
            base_currency=base_currency,
            source_reference=command.source_reference,
            entered_unit=command.unit_code,
            conversion_snapshot=conversion_snapshot,
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
        constraint = str(error.orig)
        if "stock_serial_allocations_serial_number_key" in constraint:
            raise AppError(
                409,
                "duplicate_serial_identity",
                "A Serial Identity is already assigned.",
            ) from error
        if "uq_lot_identity" in constraint:
            raise AppError(
                409,
                "lot_identity_conflict",
                "The Lot Identity already exists with conflicting attributes.",
            ) from error
        raise


@router.get(
    "/v1/inventory/availability",
    response_model=AvailabilityResponse,
    responses=error_responses(401, 403, 422, 500),
)
async def search_availability(
    actor: Annotated[AuthorizedUser, Depends(require_inventory_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    query: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AvailabilityResponse:
    if not actor.warehouse_ids:
        return AvailabilityResponse(items=[], total=0)
    filters: list[ColumnElement[bool]] = [
        inventory_availability.c.warehouse_id.in_(actor.warehouse_ids)
    ]
    if query.strip():
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                skus.c.code.ilike(pattern),
                skus.c.name.ilike(pattern),
                exists(
                    select(barcode_mappings.c.barcode_mapping_id).where(
                        barcode_mappings.c.sku_id == skus.c.sku_id,
                        barcode_mappings.c.is_active.is_(True),
                        barcode_mappings.c.barcode.ilike(pattern),
                    )
                ),
            )
        )
    statement = (
        select(
            inventory_availability,
            skus.c.code.label("sku_code"),
            skus.c.name.label("sku_name"),
            skus.c.base_stocking_unit,
            skus.c.tracking_policy,
            skus.c.expiration_control,
            select(companies.c.base_currency).limit(1).scalar_subquery().label("base_currency"),
            warehouses.c.code.label("warehouse_code"),
            warehouse_stock_locations.c.code.label("location_code"),
            warehouse_stock_locations.c.custody,
            inventory_valuation.c.inventory_value.label("warehouse_inventory_value"),
            inventory_valuation.c.moving_average_unit_cost,
        )
        .select_from(
            inventory_availability.join(skus, inventory_availability.c.sku_id == skus.c.sku_id)
            .join(warehouses, inventory_availability.c.warehouse_id == warehouses.c.warehouse_id)
            .join(
                warehouse_stock_locations,
                inventory_availability.c.location_id == warehouse_stock_locations.c.location_id,
            )
            .join(
                inventory_valuation,
                (inventory_availability.c.sku_id == inventory_valuation.c.sku_id)
                & (inventory_availability.c.warehouse_id == inventory_valuation.c.warehouse_id),
            )
        )
        .where(*filters)
        .order_by(
            skus.c.code,
            warehouses.c.code,
            warehouse_stock_locations.c.code,
            inventory_availability.c.identity_key,
        )
    )
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list((await session.execute(statement.limit(limit))).mappings())
    warehouse_on_hand_rows = (
        await session.execute(
            select(
                inventory_availability.c.sku_id,
                inventory_availability.c.warehouse_id,
                func.sum(inventory_availability.c.on_hand).label("on_hand"),
            )
            .select_from(
                inventory_availability.join(
                    warehouse_stock_locations,
                    inventory_availability.c.location_id == warehouse_stock_locations.c.location_id,
                )
            )
            .where(
                inventory_availability.c.warehouse_id.in_(actor.warehouse_ids),
                warehouse_stock_locations.c.custody.in_(("available", "dispatch_staging")),
                warehouse_stock_locations.c.is_active.is_(True),
                or_(
                    inventory_availability.c.expiration_date.is_(None),
                    inventory_availability.c.expiration_date >= date.today(),
                ),
            )
            .group_by(
                inventory_availability.c.sku_id,
                inventory_availability.c.warehouse_id,
            )
        )
    ).mappings()
    warehouse_on_hand = {
        (row["sku_id"], row["warehouse_id"]): row["on_hand"] for row in warehouse_on_hand_rows
    }
    warehouse_available_rows = (
        await session.execute(
            select(
                inventory_availability.c.sku_id,
                inventory_availability.c.warehouse_id,
                func.sum(inventory_availability.c.on_hand).label("on_hand"),
            )
            .select_from(
                inventory_availability.join(
                    warehouse_stock_locations,
                    inventory_availability.c.location_id == warehouse_stock_locations.c.location_id,
                )
            )
            .where(
                inventory_availability.c.warehouse_id.in_(actor.warehouse_ids),
                warehouse_stock_locations.c.custody == "available",
                warehouse_stock_locations.c.is_active.is_(True),
                or_(
                    inventory_availability.c.expiration_date.is_(None),
                    inventory_availability.c.expiration_date >= date.today(),
                ),
            )
            .group_by(
                inventory_availability.c.sku_id,
                inventory_availability.c.warehouse_id,
            )
        )
    ).mappings()
    warehouse_available_on_hand = {
        (row["sku_id"], row["warehouse_id"]): row["on_hand"] for row in warehouse_available_rows
    }
    commercial_reservation_rows = (
        await session.execute(
            select(inventory_reserved_by_sku_warehouse).where(
                inventory_reserved_by_sku_warehouse.c.warehouse_id.in_(actor.warehouse_ids)
            )
        )
    ).mappings()
    commercial_reserved = {
        (row["sku_id"], row["warehouse_id"]): row["reserved_quantity_base"]
        for row in commercial_reservation_rows
    }
    items: list[AvailabilityItem] = []
    for row in rows:
        warehouse_key = (row["sku_id"], row["warehouse_id"])
        order_reserved = commercial_reserved.get(warehouse_key, Decimal("0"))
        total_warehouse_on_hand = warehouse_on_hand.get(warehouse_key, Decimal("0"))
        eligible_on_hand = warehouse_available_on_hand.get(warehouse_key, Decimal("0"))
        items.append(
            AvailabilityItem(
                sku_id=row["sku_id"],
                sku_code=row["sku_code"],
                sku_name=row["sku_name"],
                warehouse_id=row["warehouse_id"],
                warehouse_code=row["warehouse_code"],
                location_code=row["location_code"],
                custody=row["custody"],
                base_stocking_unit=row["base_stocking_unit"],
                tracking_policy=row["tracking_policy"],
                expiration_control=row["expiration_control"],
                base_currency=row["base_currency"],
                lot_code=row["lot_code"] or None,
                serial_numbers=list(row["serial_numbers"]),
                expiration_date=row["expiration_date"],
                on_hand=row["on_hand"],
                reserved=row["reserved"],
                available=row["on_hand"] - row["reserved"]
                if row["custody"] == "available"
                else Decimal("0"),
                commercial_reserved=order_reserved,
                warehouse_on_hand=total_warehouse_on_hand,
                warehouse_available=max(eligible_on_hand - order_reserved, Decimal("0")),
                warehouse_inventory_value=row["warehouse_inventory_value"],
                moving_average_unit_cost=row["moving_average_unit_cost"],
            )
        )
    return AvailabilityResponse(items=items, total=total or 0)


async def rebuild_projections(
    session: AsyncSession, warehouse_ids: tuple[UUID, ...]
) -> ProjectionRebuildResponse:
    if not warehouse_ids:
        return ProjectionRebuildResponse(availability_rows=0, valuation_rows=0)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('inventory-projection-rebuild'))")
    )
    await session.execute(
        delete(inventory_availability).where(
            inventory_availability.c.warehouse_id.in_(warehouse_ids)
        )
    )
    await session.execute(
        delete(inventory_valuation).where(inventory_valuation.c.warehouse_id.in_(warehouse_ids))
    )
    movements = (
        (
            await session.execute(
                select(stock_movements)
                .order_by(stock_movements.c.posted_at, stock_movements.c.movement_id)
                .where(stock_movements.c.warehouse_id.in_(warehouse_ids))
            )
        )
        .mappings()
        .all()
    )
    for movement in movements:
        movement_line = (
            (
                await session.execute(
                    select(pick_lines).where(
                        or_(
                            pick_lines.c.source_movement_id == movement["movement_id"],
                            pick_lines.c.staging_movement_id == movement["movement_id"],
                        )
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if movement_line is None:
            delivery_line = (
                (
                    await session.execute(
                        select(delivery_lines.c.pick_line_id).where(
                            or_(
                                delivery_lines.c.staging_movement_id == movement["movement_id"],
                                delivery_lines.c.transit_movement_id == movement["movement_id"],
                            )
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if delivery_line is not None:
                movement_line = (
                    (
                        await session.execute(
                            select(pick_lines).where(
                                pick_lines.c.pick_line_id == delivery_line["pick_line_id"]
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
        if movement_line is None:
            confirmed_delivery_line = (
                (
                    await session.execute(
                        select(delivery_lines.c.pick_line_id)
                        .join(
                            delivery_confirmation_lines,
                            delivery_confirmation_lines.c.delivery_line_id
                            == delivery_lines.c.delivery_line_id,
                        )
                        .where(
                            delivery_confirmation_lines.c.outbound_movement_id
                            == movement["movement_id"]
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if confirmed_delivery_line is not None:
                movement_line = (
                    (
                        await session.execute(
                            select(pick_lines).where(
                                pick_lines.c.pick_line_id == confirmed_delivery_line["pick_line_id"]
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
        assignments = (
            (
                await session.execute(
                    select(
                        pick_identity_assignments,
                        lot_identities.c.lot_code,
                        lot_identities.c.expiration_date.label("lot_expiration_date"),
                        stock_serial_allocations.c.serial_number,
                        stock_serial_allocations.c.expiration_date.label("serial_expiration_date"),
                    )
                    .outerjoin(
                        lot_identities,
                        pick_identity_assignments.c.lot_identity_id
                        == lot_identities.c.lot_identity_id,
                    )
                    .outerjoin(
                        stock_serial_allocations,
                        pick_identity_assignments.c.serial_allocation_id
                        == stock_serial_allocations.c.serial_allocation_id,
                    )
                    .where(
                        pick_identity_assignments.c.pick_line_id == movement_line["pick_line_id"]
                    )
                )
            )
            .mappings()
            .all()
            if movement_line is not None
            else []
        )
        movement_identities = list(
            (
                await session.execute(
                    select(
                        stock_movement_identity_allocations.c.quantity_base.label(
                            "movement_identity_quantity"
                        ),
                        pick_identity_assignments.c.tracking_policy,
                        lot_identities.c.lot_code,
                        lot_identities.c.expiration_date.label("lot_expiration_date"),
                        stock_serial_allocations.c.serial_number,
                        stock_serial_allocations.c.expiration_date.label("serial_expiration_date"),
                    )
                    .join(
                        delivery_line_identity_allocations,
                        stock_movement_identity_allocations.c[
                            "delivery_line_identity_allocation_id"
                        ]
                        == delivery_line_identity_allocations.c.allocation_id,
                    )
                    .join(
                        pick_identity_assignments,
                        delivery_line_identity_allocations.c.pick_identity_assignment_id
                        == pick_identity_assignments.c.pick_identity_assignment_id,
                    )
                    .outerjoin(
                        lot_identities,
                        pick_identity_assignments.c.lot_identity_id
                        == lot_identities.c.lot_identity_id,
                    )
                    .outerjoin(
                        stock_serial_allocations,
                        pick_identity_assignments.c.serial_allocation_id
                        == stock_serial_allocations.c.serial_allocation_id,
                    )
                    .where(
                        stock_movement_identity_allocations.c.movement_id == movement["movement_id"]
                    )
                    .order_by(
                        lot_identities.c.lot_code,
                        stock_serial_allocations.c.serial_number,
                    )
                )
            ).mappings()
        )
        lot = cast(
            Mapping[str, Any] | None,
            (
                await session.execute(
                    select(
                        lot_identities.c.lot_code,
                        lot_identities.c.expiration_date,
                    )
                    .select_from(
                        stock_lot_allocations.join(
                            lot_identities,
                            stock_lot_allocations.c.lot_identity_id
                            == lot_identities.c.lot_identity_id,
                        )
                    )
                    .where(stock_lot_allocations.c.movement_id == movement["movement_id"])
                )
            )
            .mappings()
            .one_or_none(),
        )
        if lot is None:
            lot_assignment = next(
                (
                    assignment
                    for assignment in assignments
                    if assignment["tracking_policy"] == "lot"
                ),
                None,
            )
            if lot_assignment is not None:
                lot = {
                    "lot_code": lot_assignment["lot_code"],
                    "expiration_date": lot_assignment["lot_expiration_date"],
                }
        serials = cast(
            list[Mapping[str, Any]],
            (
                await session.execute(
                    select(
                        stock_serial_allocations.c.serial_number,
                        stock_serial_allocations.c.expiration_date,
                    )
                    .where(stock_serial_allocations.c.movement_id == movement["movement_id"])
                    .order_by(stock_serial_allocations.c.serial_number)
                )
            )
            .mappings()
            .all()
            if lot is None
            else [],
        )
        if not serials:
            serials = [
                {
                    "serial_number": assignment["serial_number"],
                    "expiration_date": assignment["serial_expiration_date"],
                }
                for assignment in assignments
                if assignment["tracking_policy"] == "serial"
            ]
        incoming = movement["movement_leg"] in {
            "opening_in",
            "pick_staging_in",
            "pick_reversal_available_in",
            "dispatch_transit_in",
            "exception_investigation_in",
            "return_quarantine_in",
            "recovery_quarantine_in",
        }
        signed_quantity = movement["quantity_base"] if incoming else -movement["quantity_base"]
        if movement_identities:
            for identity in movement_identities:
                is_serial = identity["tracking_policy"] == "serial"
                identity_key = (
                    f"serial:{identity['serial_number']}"
                    if is_serial
                    else f"lot:{identity['lot_code']}"
                )
                identity_quantity = cast(Decimal, identity["movement_identity_quantity"])
                identity_serials = [identity["serial_number"]] if is_serial else []
                expiration = (
                    identity["serial_expiration_date"]
                    if is_serial
                    else identity["lot_expiration_date"]
                )
                if incoming:
                    await session.execute(
                        pg_insert(inventory_availability)
                        .values(
                            sku_id=movement["sku_id"],
                            warehouse_id=movement["warehouse_id"],
                            location_id=movement["location_id"],
                            identity_key=identity_key,
                            lot_code=None if is_serial else identity["lot_code"],
                            serial_numbers=identity_serials,
                            expiration_date=expiration,
                            on_hand=identity_quantity,
                            reserved=Decimal("0"),
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                "sku_id",
                                "warehouse_id",
                                "location_id",
                                "identity_key",
                            ],
                            set_={
                                "on_hand": inventory_availability.c.on_hand + identity_quantity,
                                "serial_numbers": identity_serials,
                                "expiration_date": expiration,
                            },
                        )
                    )
                else:
                    identity_position = (
                        (
                            await session.execute(
                                select(inventory_availability)
                                .where(
                                    inventory_availability.c.sku_id == movement["sku_id"],
                                    inventory_availability.c.warehouse_id
                                    == movement["warehouse_id"],
                                    inventory_availability.c.location_id == movement["location_id"],
                                    inventory_availability.c.identity_key == identity_key,
                                )
                                .with_for_update()
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        identity_position is None
                        or identity_position["on_hand"] < identity_quantity
                    ):
                        raise AppError(
                            409,
                            "inventory_projection_rebuild_conflict",
                            "Tracked movement identity history cannot be reconciled.",
                        )
                    await session.execute(
                        update(inventory_availability)
                        .where(
                            inventory_availability.c.sku_id == movement["sku_id"],
                            inventory_availability.c.warehouse_id == movement["warehouse_id"],
                            inventory_availability.c.location_id == movement["location_id"],
                            inventory_availability.c.identity_key == identity_key,
                        )
                        .values(
                            on_hand=inventory_availability.c.on_hand - identity_quantity,
                            serial_numbers=[] if is_serial else identity_position["serial_numbers"],
                        )
                    )
        elif serials and movement["movement_leg"] != "opening_in":
            serial_numbers = sorted(serial["serial_number"] for serial in serials)
            if incoming and movement["movement_leg"] in {
                "pick_staging_in",
                "dispatch_transit_in",
            }:
                for serial in serials:
                    await session.execute(
                        pg_insert(inventory_availability).values(
                            sku_id=movement["sku_id"],
                            warehouse_id=movement["warehouse_id"],
                            location_id=movement["location_id"],
                            identity_key=f"serial:{serial['serial_number']}",
                            lot_code=None,
                            serial_numbers=[serial["serial_number"]],
                            expiration_date=serial["expiration_date"],
                            on_hand=Decimal("1"),
                            reserved=Decimal("0"),
                        )
                    )
            elif incoming:
                source_positions = (
                    (
                        await session.execute(
                            select(inventory_availability)
                            .where(
                                inventory_availability.c.sku_id == movement["sku_id"],
                                inventory_availability.c.warehouse_id == movement["warehouse_id"],
                                inventory_availability.c.location_id == movement["location_id"],
                            )
                            .order_by(inventory_availability.c.identity_key)
                            .limit(1)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if source_positions is None:
                    await session.execute(
                        insert(inventory_availability).values(
                            sku_id=movement["sku_id"],
                            warehouse_id=movement["warehouse_id"],
                            location_id=movement["location_id"],
                            identity_key=f"serial:{movement['movement_group_id']}",
                            lot_code=None,
                            serial_numbers=serial_numbers,
                            expiration_date=serials[0]["expiration_date"],
                            on_hand=movement["quantity_base"],
                            reserved=Decimal("0"),
                        )
                    )
                else:
                    await session.execute(
                        update(inventory_availability)
                        .where(
                            inventory_availability.c.sku_id == movement["sku_id"],
                            inventory_availability.c.warehouse_id == movement["warehouse_id"],
                            inventory_availability.c.location_id == movement["location_id"],
                            inventory_availability.c.identity_key
                            == source_positions["identity_key"],
                        )
                        .values(
                            on_hand=inventory_availability.c.on_hand + movement["quantity_base"],
                            serial_numbers=sorted(
                                set(source_positions["serial_numbers"]) | set(serial_numbers)
                            ),
                        )
                    )
            else:
                positions: list[dict[str, Any]] = [
                    dict(row)
                    for row in (
                        (
                            await session.execute(
                                select(inventory_availability).where(
                                    inventory_availability.c.sku_id == movement["sku_id"],
                                    inventory_availability.c.warehouse_id
                                    == movement["warehouse_id"],
                                    inventory_availability.c.location_id == movement["location_id"],
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                ]
                for serial_number in serial_numbers:
                    position_index = next(
                        (
                            index
                            for index, row in enumerate(positions)
                            if serial_number in row["serial_numbers"]
                        ),
                        None,
                    )
                    if position_index is None:
                        raise AppError(
                            409,
                            "inventory_projection_rebuild_conflict",
                            "Serial movement history cannot be reconciled.",
                        )
                    position = positions[position_index]
                    remaining_serials = sorted(set(position["serial_numbers"]) - {serial_number})
                    await session.execute(
                        update(inventory_availability)
                        .where(
                            inventory_availability.c.sku_id == movement["sku_id"],
                            inventory_availability.c.warehouse_id == movement["warehouse_id"],
                            inventory_availability.c.location_id == movement["location_id"],
                            inventory_availability.c.identity_key == position["identity_key"],
                        )
                        .values(
                            on_hand=inventory_availability.c.on_hand - Decimal("1"),
                            serial_numbers=remaining_serials,
                        )
                    )
                    positions[position_index] = {
                        **position,
                        "on_hand": position["on_hand"] - Decimal("1"),
                        "serial_numbers": remaining_serials,
                    }
        else:
            serial_expiration = serials[0]["expiration_date"] if serials else None
            identity_key = (
                f"lot:{lot['lot_code']}"
                if lot
                else f"serial:{movement['movement_id']}"
                if serials
                else ""
            )
            expiration = lot["expiration_date"] if lot else serial_expiration
            await session.execute(
                pg_insert(inventory_availability)
                .values(
                    sku_id=movement["sku_id"],
                    warehouse_id=movement["warehouse_id"],
                    location_id=movement["location_id"],
                    identity_key=identity_key,
                    lot_code=lot["lot_code"] if lot else None,
                    serial_numbers=[serial["serial_number"] for serial in serials],
                    expiration_date=expiration,
                    on_hand=signed_quantity,
                    reserved=Decimal("0"),
                )
                .on_conflict_do_update(
                    index_elements=[
                        "sku_id",
                        "warehouse_id",
                        "location_id",
                        "identity_key",
                    ],
                    set_={
                        "on_hand": inventory_availability.c.on_hand + signed_quantity,
                        "expiration_date": expiration,
                    },
                )
            )
        current = (
            (
                await session.execute(
                    select(inventory_valuation).where(
                        inventory_valuation.c.sku_id == movement["sku_id"],
                        inventory_valuation.c.warehouse_id == movement["warehouse_id"],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        quantity = signed_quantity + (current["quantity_on_hand"] if current else Decimal("0"))
        value = movement["value_delta"] + (current["inventory_value"] if current else Decimal("0"))
        average = (
            (value / quantity).quantize(SIX_PLACES, ROUND_HALF_UP)
            if quantity != Decimal("0")
            else current["moving_average_unit_cost"]
            if current
            else Decimal("0")
        )
        await session.execute(
            pg_insert(inventory_valuation)
            .values(
                sku_id=movement["sku_id"],
                warehouse_id=movement["warehouse_id"],
                quantity_on_hand=quantity,
                inventory_value=value,
                moving_average_unit_cost=average,
            )
            .on_conflict_do_update(
                index_elements=["sku_id", "warehouse_id"],
                set_={
                    "quantity_on_hand": quantity,
                    "inventory_value": value,
                    "moving_average_unit_cost": average,
                },
            )
        )
    await session.execute(
        text(
            """
            DELETE FROM delivery_exception_state state
            USING delivery_exception_cases exception_case,
                  delivery_confirmation_lines confirmation_line,
                  delivery_confirmations confirmation,
                  delivery_dispatches delivery
            WHERE state.exception_case_id = exception_case.exception_case_id
              AND exception_case.confirmation_line_id = confirmation_line.confirmation_line_id
              AND confirmation_line.confirmation_id = confirmation.confirmation_id
              AND confirmation.delivery_id = delivery.delivery_id
              AND delivery.warehouse_id = ANY(:warehouse_ids)
            """
        ),
        {"warehouse_ids": list(warehouse_ids)},
    )
    await session.execute(
        text(
            """
            INSERT INTO delivery_exception_state(
              exception_case_id, status, custody, open_quantity_base,
              returned_quantity_base, retry_allocated_quantity_base,
              resolved_quantity_base, version, updated_at
            )
            SELECT exception_case.exception_case_id,
              CASE
                WHEN exception_case.original_quantity_base - totals.closed_quantity = 0
                  THEN 'resolved'
                WHEN totals.closed_quantity > 0 THEN 'partially_resolved'
                ELSE 'open'
              END,
              CASE
                WHEN exception_case.original_quantity_base - totals.closed_quantity > 0
                  THEN exception_case.initial_custody
                ELSE coalesce(latest.to_custody, exception_case.initial_custody)
              END,
              exception_case.original_quantity_base - totals.closed_quantity,
              totals.returned_quantity,
              totals.retry_quantity,
              totals.resolved_quantity,
              totals.event_count,
              coalesce(latest.occurred_at, exception_case.opened_at)
            FROM delivery_exception_cases exception_case
            JOIN delivery_confirmation_lines confirmation_line
              ON confirmation_line.confirmation_line_id = exception_case.confirmation_line_id
            JOIN delivery_confirmations confirmation
              ON confirmation.confirmation_id = confirmation_line.confirmation_id
            JOIN delivery_dispatches delivery
              ON delivery.delivery_id = confirmation.delivery_id
            CROSS JOIN LATERAL (
              SELECT
                coalesce(sum(event.quantity_base) FILTER (
                  WHERE event.event_type = 'return_received'), 0) AS returned_quantity,
                coalesce(sum(event.quantity_base) FILTER (
                  WHERE event.event_type = 'retry_allocated'), 0) AS retry_quantity,
                coalesce(sum(event.quantity_base) FILTER (
                  WHERE event.event_type IN (
                    'recovered','carrier_claim_resolved','inventory_adjustment_resolved'
                  )), 0) AS resolved_quantity,
                coalesce(sum(event.quantity_base) FILTER (
                  WHERE event.event_type <> 'opened'), 0) AS closed_quantity,
                count(*) AS event_count
              FROM delivery_exception_events event
              WHERE event.exception_case_id = exception_case.exception_case_id
            ) totals
            LEFT JOIN LATERAL (
              SELECT event.to_custody, event.occurred_at
              FROM delivery_exception_events event
              WHERE event.exception_case_id = exception_case.exception_case_id
                AND event.event_type <> 'opened'
              ORDER BY event.occurred_at DESC, event.exception_event_id DESC
              LIMIT 1
            ) latest ON true
            WHERE delivery.warehouse_id = ANY(:warehouse_ids)
            """
        ),
        {"warehouse_ids": list(warehouse_ids)},
    )
    availability_rows = await session.scalar(
        select(func.count())
        .select_from(inventory_availability)
        .where(inventory_availability.c.warehouse_id.in_(warehouse_ids))
    )
    valuation_rows = await session.scalar(
        select(func.count())
        .select_from(inventory_valuation)
        .where(inventory_valuation.c.warehouse_id.in_(warehouse_ids))
    )
    return ProjectionRebuildResponse(
        availability_rows=availability_rows or 0,
        valuation_rows=valuation_rows or 0,
    )


@router.post(
    "/v1/inventory/projections/rebuild",
    response_model=ProjectionRebuildResponse,
    responses=error_responses(401, 403, 500),
)
async def rebuild_inventory_projections(
    actor: Annotated[AuthorizedUser, Depends(require_inventory_rebuilder)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ProjectionRebuildResponse:
    result = await rebuild_projections(session, actor.warehouse_ids)
    await session.commit()
    return result
