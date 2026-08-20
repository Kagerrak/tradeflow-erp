"""Purchase Request lifecycle: create, revise, approve, reject, and partial
conversion to PO drafts."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_purchase_order_writer,
    require_purchase_request_approver,
    require_purchase_request_reader,
    require_purchase_request_writer,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    purchase_order_lines,
    purchase_orders,
    purchase_request_lines,
    purchase_requests,
)
from tradeflow_api.purchase_orders import (
    _company_id,
    _conversion_factor,
    _resolve_branch,
    _resolve_sku,
    _resolve_supplier,
)

router = APIRouter(prefix="/v1/procurement/purchase-requests", tags=["procurement"])


class PurchaseRequestCommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PurchaseRequestLineCommand(PurchaseRequestCommandModel):
    sku_id: UUID
    requested_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_code: str = Field(min_length=1, max_length=30)
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class CreatePurchaseRequestCommand(PurchaseRequestCommandModel):
    supplier_id: UUID
    branch_id: UUID
    code: str = Field(min_length=1, max_length=50)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    exchange_rate: Decimal = Field(default=Decimal("1"), gt=0, max_digits=18, decimal_places=6)
    lines: list[PurchaseRequestLineCommand] = Field(min_length=1)


class RevisePurchaseRequestCommand(PurchaseRequestCommandModel):
    supplier_id: UUID
    branch_id: UUID
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    exchange_rate: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    lines: list[PurchaseRequestLineCommand] = Field(min_length=1)
    expected_version: int = Field(gt=0)


class ApprovePurchaseRequestCommand(PurchaseRequestCommandModel):
    expected_version: int = Field(gt=0)


class RejectPurchaseRequestCommand(PurchaseRequestCommandModel):
    expected_version: int = Field(gt=0)


class ConvertLineCommand(PurchaseRequestCommandModel):
    purchase_request_line_id: UUID
    requested_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)


class ConvertPurchaseRequestCommand(PurchaseRequestCommandModel):
    purchase_order_code: str = Field(min_length=1, max_length=50)
    lines: list[ConvertLineCommand] = Field(min_length=1)
    expected_version: int = Field(gt=0)


class PurchaseRequestLineResponse(BaseModel):
    purchase_request_line_id: UUID
    line_number: int
    sku_id: UUID
    requested_quantity: str
    unit_code: str
    base_quantity: str
    unit_cost: str
    converted_quantity: str
    open_quantity: str


class PurchaseRequestResponse(BaseModel):
    purchase_request_id: UUID
    supplier_id: UUID
    branch_id: UUID
    code: str
    currency: str
    exchange_rate: str
    status: str
    version: int
    created_by: str
    approved_by: str | None
    rejected_by: str | None
    lines: list[PurchaseRequestLineResponse]


class PurchaseRequestSummary(BaseModel):
    purchase_request_id: UUID
    supplier_id: UUID
    branch_id: UUID
    code: str
    currency: str
    status: str
    version: int


class PurchaseRequestSearchResponse(BaseModel):
    items: list[PurchaseRequestSummary]
    total: int


class ConvertedLineResponse(BaseModel):
    purchase_order_line_id: UUID
    purchase_request_line_id: UUID
    line_number: int
    requested_quantity: str


class ConversionResponse(BaseModel):
    purchase_order_id: UUID
    purchase_order_code: str
    status: str
    lines: list[ConvertedLineResponse]


def _hash_request(purchase_request_id: UUID | None, body: dict[str, Any]) -> str:
    payload: dict[str, Any] = {"body": body}
    if purchase_request_id is not None:
        payload["purchase_request_id"] = str(purchase_request_id)
    return sha256(
        "μ".join(f"{key}={value}" for key, value in sorted(payload.items())).encode()
    ).hexdigest()


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _approval_authority_id(
    session: AsyncSession,
    actor: AuthorizedUser,
    branch_id: UUID,
    capability_code: str,
    amount: Decimal,
) -> UUID:
    row = (
        (
            await session.execute(
                select(approval_authorities).where(
                    approval_authorities.c.user_subject == actor.subject,
                    approval_authorities.c.capability_code == capability_code,
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
            "No approval authority for this capability and branch.",
        )
    maximum_amount = row["maximum_amount"]
    if maximum_amount is not None and amount > maximum_amount:
        raise AppError(
            403,
            "approval_amount_exceeded",
            "Request value exceeds the approver's authority limit.",
        )
    return cast(UUID, row["approval_authority_id"])


async def _request_branch_id(session: AsyncSession, purchase_request_id: UUID) -> UUID:
    branch_id = await session.scalar(
        select(purchase_requests.c.branch_id).where(
            purchase_requests.c.purchase_request_id == purchase_request_id
        )
    )
    if branch_id is None:
        raise AppError(404, "purchase_request_not_found", "Purchase request not found.")
    return cast(UUID, branch_id)


async def _load_request(
    session: AsyncSession,
    purchase_request_id: UUID,
    company_id: UUID,
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                select(
                    purchase_requests.c.purchase_request_id,
                    purchase_requests.c.company_id,
                    purchase_requests.c.supplier_id,
                    purchase_requests.c.branch_id,
                    purchase_requests.c.code,
                    purchase_requests.c.currency,
                    purchase_requests.c.exchange_rate,
                    purchase_requests.c.status,
                    purchase_requests.c.version,
                    purchase_requests.c.created_by,
                    purchase_requests.c.approved_by,
                    purchase_requests.c.rejected_by,
                ).where(
                    purchase_requests.c.purchase_request_id == purchase_request_id,
                    purchase_requests.c.company_id == company_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def _load_request_lines(
    session: AsyncSession,
    purchase_request_id: UUID,
) -> list[dict[str, Any]]:
    converted = (
        select(
            purchase_order_lines.c.purchase_request_line_id,
            func.coalesce(func.sum(purchase_order_lines.c.requested_quantity), Decimal("0")).label(
                "converted_quantity"
            ),
        )
        .where(purchase_order_lines.c.purchase_request_line_id.is_not(None))
        .group_by(purchase_order_lines.c.purchase_request_line_id)
        .subquery()
    )
    rows = (
        (
            await session.execute(
                select(
                    purchase_request_lines.c.purchase_request_line_id,
                    purchase_request_lines.c.line_number,
                    purchase_request_lines.c.sku_id,
                    purchase_request_lines.c.requested_quantity,
                    purchase_request_lines.c.unit_code,
                    purchase_request_lines.c.base_quantity,
                    purchase_request_lines.c.unit_cost,
                    func.coalesce(converted.c.converted_quantity, Decimal("0")).label(
                        "converted_quantity"
                    ),
                )
                .select_from(purchase_request_lines)
                .outerjoin(
                    converted,
                    purchase_request_lines.c.purchase_request_line_id
                    == converted.c.purchase_request_line_id,
                )
                .where(purchase_request_lines.c.purchase_request_id == purchase_request_id)
                .order_by(purchase_request_lines.c.line_number)
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _fmt_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _request_response(
    request: dict[str, Any],
    lines: list[dict[str, Any]],
) -> PurchaseRequestResponse:
    return PurchaseRequestResponse(
        purchase_request_id=request["purchase_request_id"],
        supplier_id=request["supplier_id"],
        branch_id=request["branch_id"],
        code=request["code"],
        currency=request["currency"],
        exchange_rate=_fmt_decimal(request["exchange_rate"]),
        status=request["status"],
        version=request["version"],
        created_by=request["created_by"],
        approved_by=request["approved_by"],
        rejected_by=request["rejected_by"],
        lines=[
            PurchaseRequestLineResponse(
                purchase_request_line_id=line["purchase_request_line_id"],
                line_number=line["line_number"],
                sku_id=line["sku_id"],
                requested_quantity=_fmt_decimal(line["requested_quantity"]),
                unit_code=line["unit_code"],
                base_quantity=_fmt_decimal(line["base_quantity"]),
                unit_cost=_fmt_decimal(line["unit_cost"]),
                converted_quantity=_fmt_decimal(line["converted_quantity"]),
                open_quantity=_fmt_decimal(line["requested_quantity"] - line["converted_quantity"]),
            )
            for line in lines
        ],
    )


@router.post(
    "",
    response_model=PurchaseRequestResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 409, 422, 503),
)
async def create_purchase_request(
    request: Request,
    command: CreatePurchaseRequestCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_request_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PurchaseRequestResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")

    company_id = await _company_id(session)

    if command.branch_id not in actor.branch_ids:
        raise AppError(
            403,
            "branch_scope_required",
            "The actor is not assigned to the purchase request branch.",
        )

    await _resolve_branch(session, company_id, command.branch_id)
    await _resolve_supplier(session, company_id, command.supplier_id)

    hash_key = _hash_request(None, command.model_dump())
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=hash_key,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return PurchaseRequestResponse.model_validate(replay)

        existing = await session.scalar(
            select(purchase_requests.c.purchase_request_id).where(
                purchase_requests.c.company_id == company_id,
                purchase_requests.c.code == command.code,
            )
        )
        if existing is not None:
            raise AppError(
                409,
                "purchase_request_code_duplicate",
                "A purchase request with this code already exists.",
            )

        line_inputs: list[dict[str, Any]] = []
        purchase_request_id = uuid4()
        for index, line in enumerate(command.lines, start=1):
            await _resolve_sku(session, line.sku_id)
            factor = await _conversion_factor(session, line.sku_id, line.unit_code)
            line_inputs.append(
                {
                    "purchase_request_line_id": uuid4(),
                    "purchase_request_id": purchase_request_id,
                    "line_number": index,
                    "sku_id": line.sku_id,
                    "requested_quantity": line.requested_quantity,
                    "unit_code": line.unit_code,
                    "base_quantity": line.requested_quantity * factor,
                    "unit_cost": line.unit_cost,
                }
            )

        correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
        await session.execute(
            insert(purchase_requests).values(
                purchase_request_id=purchase_request_id,
                company_id=company_id,
                supplier_id=command.supplier_id,
                branch_id=command.branch_id,
                code=command.code,
                currency=command.currency,
                exchange_rate=command.exchange_rate,
                status="draft",
                version=1,
                created_by=actor.subject,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        )
        await session.execute(insert(purchase_request_lines), line_inputs)

        request_row = await _load_request(session, purchase_request_id, company_id)
        if request_row is None:
            raise AppError(500, "internal_error", "Purchase request could not be loaded.")
        request_lines = await _load_request_lines(session, purchase_request_id)
        result = _request_response(request_row, request_lines)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=hash_key,
            result=result,
        )

    return result


@router.get(
    "",
    response_model=PurchaseRequestSearchResponse,
    responses=error_responses(401, 403, 503),
)
async def list_purchase_requests(
    actor: Annotated[AuthorizedUser, Depends(require_purchase_request_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    query: Annotated[str, Query(max_length=200)] = "",
    status: Annotated[str, Query(max_length=30)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PurchaseRequestSearchResponse:
    company_id = await _company_id(session)

    filters = [
        purchase_requests.c.company_id == company_id,
        purchase_requests.c.branch_id.in_(actor.branch_ids),
    ]
    if query.strip():
        filters.append(purchase_requests.c.code.ilike(f"%{query.strip()}%"))
    if status.strip():
        filters.append(purchase_requests.c.status == status.strip())

    total = await session.scalar(
        select(func.count(purchase_requests.c.purchase_request_id)).where(*filters)
    )

    rows = (
        (
            await session.execute(
                select(
                    purchase_requests.c.purchase_request_id,
                    purchase_requests.c.supplier_id,
                    purchase_requests.c.branch_id,
                    purchase_requests.c.code,
                    purchase_requests.c.currency,
                    purchase_requests.c.status,
                    purchase_requests.c.version,
                )
                .where(*filters)
                .order_by(purchase_requests.c.code)
                .limit(limit)
                .offset(offset)
            )
        )
        .mappings()
        .all()
    )

    return PurchaseRequestSearchResponse(
        items=[
            PurchaseRequestSummary(
                purchase_request_id=row["purchase_request_id"],
                supplier_id=row["supplier_id"],
                branch_id=row["branch_id"],
                code=row["code"],
                currency=row["currency"],
                status=row["status"],
                version=row["version"],
            )
            for row in rows
        ],
        total=total or 0,
    )


@router.get(
    "/{purchase_request_id}",
    response_model=PurchaseRequestResponse,
    responses=error_responses(401, 403, 404, 503),
)
async def get_purchase_request(
    purchase_request_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_request_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PurchaseRequestResponse:
    company_id = await _company_id(session)
    request_row = await _load_request(session, purchase_request_id, company_id)
    if request_row is None:
        raise AppError(404, "purchase_request_not_found", "Purchase request not found.")
    if request_row["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "branch_scope_required",
            "The actor is not assigned to the purchase request branch.",
        )
    lines = await _load_request_lines(session, purchase_request_id)
    return _request_response(request_row, lines)


@router.put(
    "/{purchase_request_id}",
    response_model=PurchaseRequestResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
)
async def revise_purchase_request(
    purchase_request_id: UUID,
    command: RevisePurchaseRequestCommand,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_request_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PurchaseRequestResponse:
    company_id = await _company_id(session)

    await session.rollback()
    async with session.begin():
        request_row = await session.execute(
            select(
                purchase_requests.c.purchase_request_id,
                purchase_requests.c.branch_id,
                purchase_requests.c.status,
                purchase_requests.c.version,
            ).where(
                purchase_requests.c.purchase_request_id == purchase_request_id,
                purchase_requests.c.company_id == company_id,
            )
        )
        request = request_row.mappings().one_or_none()
        if request is None:
            raise AppError(404, "purchase_request_not_found", "Purchase request not found.")
        if request["branch_id"] not in actor.branch_ids:
            raise AppError(
                403,
                "branch_scope_required",
                "The actor is not assigned to the purchase request branch.",
            )
        if request["status"] not in ("draft", "rejected"):
            raise AppError(
                409,
                "purchase_request_not_editable",
                "Only draft or rejected requests can be revised.",
            )
        if request["version"] != command.expected_version:
            raise AppError(
                409,
                "purchase_request_version_conflict",
                "The purchase request has changed and requires refresh.",
            )

        await _resolve_branch(session, company_id, command.branch_id)
        await _resolve_supplier(session, company_id, command.supplier_id)

        await session.execute(
            purchase_request_lines.delete().where(
                purchase_request_lines.c.purchase_request_id == purchase_request_id
            )
        )

        line_inputs: list[dict[str, Any]] = []
        for index, line in enumerate(command.lines, start=1):
            await _resolve_sku(session, line.sku_id)
            factor = await _conversion_factor(session, line.sku_id, line.unit_code)
            line_inputs.append(
                {
                    "purchase_request_line_id": uuid4(),
                    "purchase_request_id": purchase_request_id,
                    "line_number": index,
                    "sku_id": line.sku_id,
                    "requested_quantity": line.requested_quantity,
                    "unit_code": line.unit_code,
                    "base_quantity": line.requested_quantity * factor,
                    "unit_cost": line.unit_cost,
                }
            )
        await session.execute(insert(purchase_request_lines), line_inputs)

        await session.execute(
            purchase_requests.update()
            .where(purchase_requests.c.purchase_request_id == purchase_request_id)
            .values(
                supplier_id=command.supplier_id,
                branch_id=command.branch_id,
                currency=command.currency,
                exchange_rate=command.exchange_rate,
                status="draft",
                version=purchase_requests.c.version + 1,
            )
        )

    updated = await _load_request(session, purchase_request_id, company_id)
    if updated is None:
        raise AppError(500, "internal_error", "Purchase request could not be loaded.")
    lines = await _load_request_lines(session, purchase_request_id)
    return _request_response(updated, lines)


@router.post(
    "/{purchase_request_id}/approve",
    response_model=PurchaseRequestResponse,
    responses=error_responses(400, 401, 403, 404, 409, 503),
)
async def approve_purchase_request(
    purchase_request_id: UUID,
    command: ApprovePurchaseRequestCommand,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_request_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PurchaseRequestResponse:
    company_id = await _company_id(session)

    await session.rollback()
    async with session.begin():
        request_row = await session.execute(
            select(
                purchase_requests.c.purchase_request_id,
                purchase_requests.c.branch_id,
                purchase_requests.c.status,
                purchase_requests.c.version,
                purchase_requests.c.created_by,
            ).where(
                purchase_requests.c.purchase_request_id == purchase_request_id,
                purchase_requests.c.company_id == company_id,
            )
        )
        request = request_row.mappings().one_or_none()
        if request is None:
            raise AppError(404, "purchase_request_not_found", "Purchase request not found.")
        if request["branch_id"] not in actor.branch_ids:
            raise AppError(
                403,
                "branch_scope_required",
                "The actor is not assigned to the purchase request branch.",
            )
        if request["status"] not in ("draft", "submitted"):
            raise AppError(
                409,
                "purchase_request_not_approvable",
                "Only draft or submitted requests can be approved.",
            )
        if request["version"] != command.expected_version:
            raise AppError(
                409,
                "purchase_request_version_conflict",
                "The purchase request has changed and requires refresh.",
            )
        if request["created_by"] == actor.subject:
            raise AppError(
                409,
                "purchase_request_maker_checker",
                "A maker cannot approve their own purchase request.",
            )

        lines = await _load_request_lines(session, purchase_request_id)
        total_value = sum(line["requested_quantity"] * line["unit_cost"] for line in lines)
        await _approval_authority_id(
            session,
            actor,
            request["branch_id"],
            "procurement:purchase-request-approve",
            total_value,
        )

        await session.execute(
            purchase_requests.update()
            .where(purchase_requests.c.purchase_request_id == purchase_request_id)
            .values(
                status="approved",
                approved_by=actor.subject,
                version=purchase_requests.c.version + 1,
            )
        )

    updated = await _load_request(session, purchase_request_id, company_id)
    if updated is None:
        raise AppError(500, "internal_error", "Purchase request could not be loaded.")
    lines = await _load_request_lines(session, purchase_request_id)
    return _request_response(updated, lines)


@router.post(
    "/{purchase_request_id}/reject",
    response_model=PurchaseRequestResponse,
    responses=error_responses(400, 401, 403, 404, 409, 503),
)
async def reject_purchase_request(
    purchase_request_id: UUID,
    command: RejectPurchaseRequestCommand,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_request_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PurchaseRequestResponse:
    company_id = await _company_id(session)

    await session.rollback()
    async with session.begin():
        request_row = await session.execute(
            select(
                purchase_requests.c.purchase_request_id,
                purchase_requests.c.branch_id,
                purchase_requests.c.status,
                purchase_requests.c.version,
                purchase_requests.c.created_by,
            ).where(
                purchase_requests.c.purchase_request_id == purchase_request_id,
                purchase_requests.c.company_id == company_id,
            )
        )
        request = request_row.mappings().one_or_none()
        if request is None:
            raise AppError(404, "purchase_request_not_found", "Purchase request not found.")
        if request["branch_id"] not in actor.branch_ids:
            raise AppError(
                403,
                "branch_scope_required",
                "The actor is not assigned to the purchase request branch.",
            )
        if request["status"] not in ("draft", "submitted"):
            raise AppError(
                409,
                "purchase_request_not_rejectable",
                "Only draft or submitted requests can be rejected.",
            )
        if request["version"] != command.expected_version:
            raise AppError(
                409,
                "purchase_request_version_conflict",
                "The purchase request has changed and requires refresh.",
            )
        if request["created_by"] == actor.subject:
            raise AppError(
                409,
                "purchase_request_maker_checker",
                "A maker cannot reject their own purchase request.",
            )

        await session.execute(
            purchase_requests.update()
            .where(purchase_requests.c.purchase_request_id == purchase_request_id)
            .values(
                status="rejected",
                rejected_by=actor.subject,
                version=purchase_requests.c.version + 1,
            )
        )

    updated = await _load_request(session, purchase_request_id, company_id)
    if updated is None:
        raise AppError(500, "internal_error", "Purchase request could not be loaded.")
    lines = await _load_request_lines(session, purchase_request_id)
    return _request_response(updated, lines)


@router.post(
    "/{purchase_request_id}/conversions",
    response_model=ConversionResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
)
async def convert_purchase_request(
    purchase_request_id: UUID,
    request: Request,
    command: ConvertPurchaseRequestCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_order_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> ConversionResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")

    company_id = await _company_id(session)
    hash_key = _hash_request(purchase_request_id, command.model_dump())

    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=hash_key,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return ConversionResponse.model_validate(replay)

        await _lock(session, f"purchase-request-conversion:{purchase_request_id}")

        request_row = await _load_request(session, purchase_request_id, company_id)
        if request_row is None:
            raise AppError(404, "purchase_request_not_found", "Purchase request not found.")
        if request_row["branch_id"] not in actor.branch_ids:
            raise AppError(
                403,
                "branch_scope_required",
                "The actor is not assigned to the purchase request branch.",
            )
        if request_row["status"] not in ("approved", "partially_converted"):
            raise AppError(
                409,
                "purchase_request_not_convertible",
                "Only approved or partially converted requests can be converted.",
            )
        if request_row["version"] != command.expected_version:
            raise AppError(
                409,
                "purchase_request_version_conflict",
                "The purchase request has changed and requires refresh.",
            )

        existing_po = await session.scalar(
            select(purchase_orders.c.purchase_order_id).where(
                purchase_orders.c.company_id == company_id,
                purchase_orders.c.code == command.purchase_order_code,
            )
        )
        if existing_po is not None:
            raise AppError(
                409,
                "purchase_order_code_duplicate",
                "A purchase order with this code already exists.",
            )

        loaded_lines = await _load_request_lines(session, purchase_request_id)
        request_lines = {line["purchase_request_line_id"]: line for line in loaded_lines}
        line_inputs: list[dict[str, Any]] = []
        converted_line_ids: set[UUID] = set()
        purchase_order_id = uuid4()
        for index, convert_line in enumerate(command.lines, start=1):
            request_line = request_lines.get(convert_line.purchase_request_line_id)
            if request_line is None:
                raise AppError(
                    422,
                    "purchase_request_line_not_found",
                    "Conversion references an unknown request line.",
                )
            open_quantity = request_line["requested_quantity"] - request_line["converted_quantity"]
            if convert_line.requested_quantity > open_quantity:
                raise AppError(
                    409,
                    "purchase_request_overconverted",
                    "Conversion quantity exceeds the open request line quantity.",
                )
            if convert_line.purchase_request_line_id in converted_line_ids:
                raise AppError(
                    409,
                    "purchase_request_duplicate_conversion_line",
                    "A request line can only appear once per conversion.",
                )
            converted_line_ids.add(convert_line.purchase_request_line_id)

            await _resolve_sku(session, request_line["sku_id"])
            factor = await _conversion_factor(
                session,
                request_line["sku_id"],
                request_line["unit_code"],
            )
            po_line_id = uuid4()
            line_inputs.append(
                {
                    "purchase_order_line_id": po_line_id,
                    "purchase_order_id": purchase_order_id,
                    "purchase_request_line_id": convert_line.purchase_request_line_id,
                    "line_number": index,
                    "sku_id": request_line["sku_id"],
                    "requested_quantity": convert_line.requested_quantity,
                    "unit_code": request_line["unit_code"],
                    "base_quantity": convert_line.requested_quantity * factor,
                    "unit_cost": request_line["unit_cost"],
                }
            )

        await session.execute(
            insert(purchase_orders).values(
                purchase_order_id=purchase_order_id,
                company_id=company_id,
                supplier_id=request_row["supplier_id"],
                branch_id=request_row["branch_id"],
                code=command.purchase_order_code,
                currency=request_row["currency"],
                exchange_rate=request_row["exchange_rate"],
                status="draft",
                version=1,
                created_by=actor.subject,
            )
        )
        await session.execute(insert(purchase_order_lines), line_inputs)

        refreshed_lines = await _load_request_lines(session, purchase_request_id)
        all_fully_converted = all(
            line["converted_quantity"] >= line["requested_quantity"] for line in refreshed_lines
        )
        new_status = "fully_converted" if all_fully_converted else "partially_converted"
        await session.execute(
            purchase_requests.update()
            .where(purchase_requests.c.purchase_request_id == purchase_request_id)
            .values(status=new_status, version=purchase_requests.c.version + 1)
        )

        result = ConversionResponse(
            purchase_order_id=purchase_order_id,
            purchase_order_code=command.purchase_order_code,
            status="draft",
            lines=[
                ConvertedLineResponse(
                    purchase_order_line_id=line["purchase_order_line_id"],
                    purchase_request_line_id=line["purchase_request_line_id"],
                    line_number=line["line_number"],
                    requested_quantity=_fmt_decimal(line["requested_quantity"]),
                )
                for line in line_inputs
            ],
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=hash_key,
            result=result,
        )

    return result
