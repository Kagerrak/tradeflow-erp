from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_return_authorizer,
    require_return_evidence_capturer,
    require_return_evidence_reader,
    require_return_reader,
    require_return_requester,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    companies,
    delivery_confirmation_lines,
    delivery_confirmations,
    delivery_correction_lines,
    delivery_corrections,
    delivery_dispatches,
    delivery_receipts,
    return_authorizations,
    return_reasons,
    return_request_evidence,
    return_request_evidence_sync_state,
    return_request_lines,
    return_requests,
    return_responsible_parties,
    sales_order_line_revisions,
    sales_order_revisions,
)
from tradeflow_api.money import currency_quantum
from tradeflow_api.object_storage import ObjectStorage, UploadedPart, get_object_storage

router = APIRouter(tags=["returns"])
ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")
NonBlankCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
NonBlankLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


def _id(kind: str, source: UUID | str) -> UUID:
    return uuid5(NAMESPACE_URL, f"tradeflow:{kind}:{source}")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReturnRequestLineCommand(CommandModel):
    delivery_line_id: UUID
    quantity_base: Decimal = Field(gt=0, max_digits=18, decimal_places=6)


class CreateReturnRequest(CommandModel):
    return_request_id: UUID
    reason_code: NonBlankCode
    reason_label: NonBlankLabel
    responsible_party_code: NonBlankCode
    responsible_party_label: NonBlankLabel
    notes: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ] = None
    lines: list[ReturnRequestLineCommand] = Field(min_length=1)


class AuthorizeReturnRequest(CommandModel):
    expected_request_version: int = Field(gt=0)


class ReturnRequestLineResponse(BaseModel):
    delivery_line_id: UUID
    line_id: UUID
    sku_id: UUID
    quantity_base: Decimal
    delivered_quantity_base: Decimal
    eligible_quantity_base: Decimal


class ReturnRequestResponse(BaseModel):
    return_request_id: UUID
    delivery_receipt_id: UUID
    confirmation_id: UUID
    delivery_id: UUID
    branch_id: UUID
    warehouse_id: UUID
    status: Literal["pending_authorization", "authorized"]
    version: int
    reason_code: str
    reason_label: str
    responsible_party_code: str
    responsible_party_label: str
    notes: str | None
    requested_by: str
    requested_at: datetime
    authorized_by: str | None
    authorized_at: datetime | None
    affected_value_base_currency: Decimal
    base_currency: str
    lines: list[ReturnRequestLineResponse]


class ReturnRequestList(BaseModel):
    items: list[ReturnRequestResponse]
    total: int


class ReturnClassification(BaseModel):
    code: str
    label: str


class ReturnClassificationsResponse(BaseModel):
    reasons: list[ReturnClassification]
    responsible_parties: list[ReturnClassification]


class ReturnEligibleLine(BaseModel):
    delivery_line_id: UUID
    line_id: UUID
    sku_id: UUID
    delivered_quantity_base: Decimal
    eligible_quantity_base: Decimal


class ReturnEligibilityResponse(BaseModel):
    delivery_receipt_id: UUID
    number: str
    lines: list[ReturnEligibleLine]


class ReturnEvidenceUploadIntent(CommandModel):
    evidence_id: UUID
    kind: Literal["photo"]
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: int = Field(gt=0, le=10 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_captured_at: datetime


class EvidenceUploadPartResponse(BaseModel):
    part_number: int
    start_byte: int
    end_byte: int
    upload_url: str
    upload_headers: dict[str, str]


class EvidenceUploadResponse(BaseModel):
    evidence_id: UUID
    status: Literal["uploading", "verified"]
    upload_id: str | None
    part_size: int | None
    parts: list[EvidenceUploadPartResponse]
    expires_at: datetime | None


class SignedAccessResponse(BaseModel):
    access_url: str
    expires_at: datetime


class ReturnEvidenceNoteCommand(CommandModel):
    evidence_id: UUID
    device_captured_at: datetime
    note_text: str = Field(min_length=1, max_length=2000)


class ReturnEvidenceItem(BaseModel):
    evidence_id: UUID
    kind: Literal["photo", "note"]
    status: Literal["uploading", "verified", "rejected"]
    content_type: str | None
    size_bytes: int | None
    sha256: str | None
    note_text: str | None
    captured_by: str
    device_captured_at: datetime
    created_at: datetime
    verified_at: datetime | None


class ReturnEvidenceList(BaseModel):
    items: list[ReturnEvidenceItem]


class OfflineEvidenceEntry(CommandModel):
    evidence_id: UUID
    kind: Literal["photo", "note"]
    note_text: str | None = Field(default=None, max_length=2000)


class SyncOfflineEvidenceCommand(CommandModel):
    expected_request_version: int = Field(gt=0)
    correlation_id: str = Field(min_length=1, max_length=100)
    evidence: list[OfflineEvidenceEntry] = Field(min_length=1)


class ReturnEvidenceSyncState(BaseModel):
    return_request_id: UUID
    expected_version: int
    current_version: int
    status: Literal["acknowledged", "conflict", "pending"]
    acknowledged_at: datetime | None
    conflict_detected_at: datetime | None
    conflict_reason: str | None


@router.get(
    "/v1/return-classifications",
    response_model=ReturnClassificationsResponse,
    responses=error_responses(401, 403, 500),
)
async def list_return_classifications(
    actor: Annotated[AuthorizedUser, Depends(require_return_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReturnClassificationsResponse:
    del actor
    reasons = (
        (
            await session.execute(
                select(return_reasons.c.code, return_reasons.c.label)
                .where(return_reasons.c.is_active)
                .order_by(return_reasons.c.label)
            )
        )
        .mappings()
        .all()
    )
    parties = (
        (
            await session.execute(
                select(
                    return_responsible_parties.c.code,
                    return_responsible_parties.c.label,
                )
                .where(return_responsible_parties.c.is_active)
                .order_by(return_responsible_parties.c.label)
            )
        )
        .mappings()
        .all()
    )
    return ReturnClassificationsResponse(
        reasons=[ReturnClassification(**row) for row in reasons],
        responsible_parties=[ReturnClassification(**row) for row in parties],
    )


def _request_hash(receipt_id: UUID, actor: str, command: BaseModel) -> str:
    raw = f"return-request:{receipt_id}:{actor}:{command.model_dump_json(exclude_none=False)}"
    return sha256(raw.encode()).hexdigest()


def _authorization_hash(request_id: UUID, actor: str, command: BaseModel) -> str:
    raw = f"return-authorization:{request_id}:{actor}:{command.model_dump_json()}"
    return sha256(raw.encode()).hexdigest()


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
    )


async def _receipt(
    session: AsyncSession, receipt_id: UUID, actor: AuthorizedUser
) -> Mapping[str, Any]:
    row = (
        (
            await session.execute(
                select(
                    delivery_receipts,
                    delivery_dispatches.c.delivery_id,
                    delivery_dispatches.c.warehouse_id,
                    delivery_dispatches.c.sales_order_revision_id,
                )
                .join(
                    delivery_confirmations,
                    delivery_receipts.c.confirmation_id == delivery_confirmations.c.confirmation_id,
                )
                .join(
                    delivery_dispatches,
                    delivery_confirmations.c.delivery_id == delivery_dispatches.c.delivery_id,
                )
                .where(delivery_receipts.c.delivery_receipt_id == receipt_id)
                .with_for_update(of=delivery_receipts)
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "delivery_receipt_not_found", "Delivery Receipt does not exist.")
    if row["branch_id"] not in actor.branch_ids or row["warehouse_id"] not in actor.warehouse_ids:
        raise AppError(
            403, "operational_scope_required", "Branch and Warehouse scope are required."
        )
    return cast(Mapping[str, Any], row)


async def _assert_current_receipt(session: AsyncSession, receipt: Mapping[str, Any]) -> None:
    successor_receipt = await session.scalar(
        select(delivery_receipts.c.delivery_receipt_id).where(
            delivery_receipts.c.corrects_delivery_receipt_id == receipt["delivery_receipt_id"]
        )
    )
    pending_correction = await session.scalar(
        select(delivery_corrections.c.correction_id).where(
            delivery_corrections.c.original_delivery_receipt_id == receipt["delivery_receipt_id"]
        )
    )
    if successor_receipt is not None or pending_correction is not None:
        raise AppError(
            409,
            "return_request_receipt_conflict",
            "Only the current Delivery Receipt without a pending correction may be returned.",
        )


async def _source_lines(
    session: AsyncSession, receipt: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    source_correction_id = cast(UUID | None, receipt.get("correction_id"))
    source_table = (
        delivery_correction_lines
        if source_correction_id is not None
        else delivery_confirmation_lines
    )
    owner_filter = (
        source_table.c.correction_id == source_correction_id
        if source_correction_id is not None
        else source_table.c.confirmation_id == receipt["confirmation_id"]
    )
    rows = list(
        (
            await session.execute(
                select(
                    source_table.c.delivery_line_id,
                    source_table.c.line_id,
                    source_table.c.sku_id,
                    source_table.c.accepted_quantity_base.label("delivered_quantity_base"),
                    sales_order_line_revisions.c.quantity_base.label("ordered_quantity_base"),
                    sales_order_line_revisions.c.line_total,
                    sales_order_revisions.c.currency,
                )
                .join(
                    sales_order_line_revisions,
                    (
                        sales_order_line_revisions.c.sales_order_revision_id
                        == receipt["sales_order_revision_id"]
                    )
                    & (sales_order_line_revisions.c.line_id == source_table.c.line_id),
                )
                .join(
                    sales_order_revisions,
                    sales_order_revisions.c.sales_order_revision_id
                    == sales_order_line_revisions.c.sales_order_revision_id,
                )
                .where(owner_filter)
                .order_by(source_table.c.delivery_line_id)
            )
        ).mappings()
    )
    return [cast(Mapping[str, Any], row) for row in rows]


async def _authorized_by_line(session: AsyncSession, receipt_id: UUID) -> dict[UUID, Decimal]:
    rows = (
        await session.execute(
            select(
                return_request_lines.c.delivery_line_id,
                func.sum(return_request_lines.c.quantity_base).label("quantity_base"),
            )
            .join(
                return_requests,
                return_request_lines.c.return_request_id == return_requests.c.return_request_id,
            )
            .join(
                return_authorizations,
                return_authorizations.c.return_request_id == return_requests.c.return_request_id,
            )
            .where(return_requests.c.delivery_receipt_id == receipt_id)
            .group_by(return_request_lines.c.delivery_line_id)
        )
    ).mappings()
    return {row["delivery_line_id"]: row["quantity_base"] for row in rows}


@router.get(
    "/v1/delivery-receipts/{receipt_id}/return-eligibility",
    response_model=ReturnEligibilityResponse,
    responses=error_responses(401, 403, 404, 409, 500),
)
async def get_return_eligibility(
    receipt_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_return_requester)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReturnEligibilityResponse:
    receipt = await _receipt(session, receipt_id, actor)
    await _assert_current_receipt(session, receipt)
    sources = await _source_lines(session, receipt)
    authorized = await _authorized_by_line(session, receipt_id)
    return ReturnEligibilityResponse(
        delivery_receipt_id=receipt_id,
        number=receipt["number"],
        lines=[
            ReturnEligibleLine(
                delivery_line_id=source["delivery_line_id"],
                line_id=source["line_id"],
                sku_id=source["sku_id"],
                delivered_quantity_base=source["delivered_quantity_base"],
                eligible_quantity_base=max(
                    ZERO,
                    source["delivered_quantity_base"]
                    - authorized.get(source["delivery_line_id"], ZERO),
                ),
            )
            for source in sources
        ],
    )


async def _responses(session: AsyncSession, request_ids: list[UUID]) -> list[ReturnRequestResponse]:
    if not request_ids:
        return []
    rows = list(
        (
            await session.execute(
                select(
                    return_requests,
                    return_authorizations.c.authorized_by,
                    return_authorizations.c.authorized_at,
                )
                .outerjoin(
                    return_authorizations,
                    return_authorizations.c.return_request_id
                    == return_requests.c.return_request_id,
                )
                .where(return_requests.c.return_request_id.in_(request_ids))
            )
        ).mappings()
    )
    receipt_ids = {row["delivery_receipt_id"] for row in rows}
    authorized_rows = (
        await session.execute(
            select(
                return_requests.c.delivery_receipt_id,
                return_request_lines.c.delivery_line_id,
                func.sum(return_request_lines.c.quantity_base).label("quantity_base"),
            )
            .join(
                return_request_lines,
                return_request_lines.c.return_request_id == return_requests.c.return_request_id,
            )
            .join(
                return_authorizations,
                return_authorizations.c.return_request_id == return_requests.c.return_request_id,
            )
            .where(return_requests.c.delivery_receipt_id.in_(receipt_ids))
            .group_by(
                return_requests.c.delivery_receipt_id,
                return_request_lines.c.delivery_line_id,
            )
        )
    ).mappings()
    authorized_by_line = {
        (row["delivery_receipt_id"], row["delivery_line_id"]): row["quantity_base"]
        for row in authorized_rows
    }
    line_rows = (
        await session.execute(
            select(return_request_lines)
            .where(return_request_lines.c.return_request_id.in_(request_ids))
            .order_by(
                return_request_lines.c.return_request_id,
                return_request_lines.c.delivery_line_id,
            )
        )
    ).mappings()
    lines_by_request: dict[UUID, list[Mapping[str, Any]]] = {}
    for line in line_rows:
        lines_by_request.setdefault(line["return_request_id"], []).append(
            cast(Mapping[str, Any], line)
        )
    rows_by_id = {row["return_request_id"]: row for row in rows}
    responses: list[ReturnRequestResponse] = []
    for request_id in request_ids:
        row = rows_by_id[request_id]
        authorized = row["authorized_by"] is not None
        responses.append(
            ReturnRequestResponse(
                return_request_id=row["return_request_id"],
                delivery_receipt_id=row["delivery_receipt_id"],
                confirmation_id=row["confirmation_id"],
                delivery_id=row["delivery_id"],
                branch_id=row["branch_id"],
                warehouse_id=row["warehouse_id"],
                status="authorized" if authorized else "pending_authorization",
                version=2 if authorized else 1,
                reason_code=row["reason_code"],
                reason_label=row["reason_label"],
                responsible_party_code=row["responsible_party_code"],
                responsible_party_label=row["responsible_party_label"],
                notes=row["notes"],
                requested_by=row["requested_by"],
                requested_at=row["requested_at"],
                authorized_by=row["authorized_by"],
                authorized_at=row["authorized_at"],
                affected_value_base_currency=row["affected_value_base_currency"],
                base_currency=row["base_currency"],
                lines=[
                    ReturnRequestLineResponse(
                        delivery_line_id=line["delivery_line_id"],
                        line_id=line["line_id"],
                        sku_id=line["sku_id"],
                        quantity_base=line["quantity_base"],
                        delivered_quantity_base=line["delivered_quantity_base"],
                        eligible_quantity_base=max(
                            ZERO,
                            line["delivered_quantity_base"]
                            - authorized_by_line.get(
                                (row["delivery_receipt_id"], line["delivery_line_id"]), ZERO
                            ),
                        ),
                    )
                    for line in lines_by_request.get(request_id, [])
                ],
            )
        )
    return responses


async def _response(session: AsyncSession, request_id: UUID) -> ReturnRequestResponse:
    return (await _responses(session, [request_id]))[0]


@router.post(
    "/v1/delivery-receipts/{receipt_id}/return-requests",
    response_model=ReturnRequestResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def create_return_request(
    receipt_id: UUID,
    command: CreateReturnRequest,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_return_requester)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200, pattern=r".*\S.*"),
    ] = None,
) -> ReturnRequestResponse:
    if not idempotency_key:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash(receipt_id, actor.subject, command)
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery-receipt-chain:{receipt_id}")
        receipt = await _receipt(session, receipt_id, actor)
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 201
            response.headers["X-Idempotency-Replayed"] = "true"
            return ReturnRequestResponse.model_validate(replay)
        await _assert_current_receipt(session, receipt)
        sources = await _source_lines(session, receipt)
        supplied = {line.delivery_line_id: line for line in command.lines}
        if len(supplied) != len(command.lines):
            raise AppError(
                409, "return_request_line_conflict", "Each Delivery Line may appear once."
            )
        by_id = {source["delivery_line_id"]: source for source in sources}
        if not set(supplied).issubset(by_id):
            raise AppError(
                409,
                "return_request_line_conflict",
                "Every Return Request line must belong to the current Delivery Receipt.",
            )
        base_currency = cast(str, await session.scalar(select(companies.c.base_currency).limit(1)))
        if any(source["currency"] != base_currency for source in sources):
            raise AppError(
                409,
                "return_request_currency_conflict",
                "Return approval value requires a Base Currency sales snapshot.",
            )
        reason = (
            await session.execute(
                select(return_reasons.c.code).where(
                    return_reasons.c.code == command.reason_code,
                    return_reasons.c.label == command.reason_label,
                    return_reasons.c.is_active.is_(True),
                )
            )
        ).one_or_none()
        responsibility = (
            await session.execute(
                select(return_responsible_parties.c.code).where(
                    return_responsible_parties.c.code == command.responsible_party_code,
                    return_responsible_parties.c.label == command.responsible_party_label,
                    return_responsible_parties.c.is_active.is_(True),
                )
            )
        ).one_or_none()
        if reason is None or responsibility is None:
            raise AppError(
                409,
                "return_classification_invalid",
                "Return reason and responsible party must be active controlled classifications.",
            )
        quantum = currency_quantum(base_currency)
        line_rows: list[dict[str, object]] = []
        affected = ZERO
        for delivery_line_id, line in supplied.items():
            source = by_id[delivery_line_id]
            if line.quantity_base > source["delivered_quantity_base"]:
                raise AppError(
                    409,
                    "return_quantity_exceeds_delivered",
                    "Requested return quantity exceeds Delivered Quantity.",
                )
            line_value = (
                source["line_total"] * line.quantity_base / source["ordered_quantity_base"]
            ).quantize(quantum, ROUND_HALF_UP)
            affected += line_value
            line_rows.append(
                {
                    "return_request_line_id": _id(
                        "return-request-line", f"{command.return_request_id}:{delivery_line_id}"
                    ),
                    "return_request_id": command.return_request_id,
                    "delivery_line_id": delivery_line_id,
                    "line_id": source["line_id"],
                    "sku_id": source["sku_id"],
                    "quantity_base": line.quantity_base,
                    "delivered_quantity_base": source["delivered_quantity_base"],
                    "affected_value_base_currency": line_value,
                }
            )
        await session.execute(
            insert(return_requests).values(
                return_request_id=command.return_request_id,
                delivery_receipt_id=receipt_id,
                confirmation_id=receipt["confirmation_id"],
                delivery_id=receipt["delivery_id"],
                branch_id=receipt["branch_id"],
                warehouse_id=receipt["warehouse_id"],
                reason_code=command.reason_code,
                reason_label=command.reason_label,
                responsible_party_code=command.responsible_party_code,
                responsible_party_label=command.responsible_party_label,
                notes=command.notes,
                requested_by=actor.subject,
                base_currency=base_currency,
                affected_value_base_currency=affected.quantize(SIX_PLACES),
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        await session.execute(insert(return_request_lines), line_rows)
        await session.execute(
            update(return_requests)
            .where(return_requests.c.return_request_id == command.return_request_id)
            .values(sealed_at=func.now())
        )
        result = await _response(session, command.return_request_id)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        response.headers["X-Idempotency-Replayed"] = "false"
    return result


@router.get(
    "/v1/return-requests/{return_request_id}",
    response_model=ReturnRequestResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_return_request(
    return_request_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_return_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReturnRequestResponse:
    scope = (
        (
            await session.execute(
                select(return_requests.c.branch_id, return_requests.c.warehouse_id).where(
                    return_requests.c.return_request_id == return_request_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if scope is None:
        raise AppError(404, "return_request_not_found", "Return Request does not exist.")
    if (
        scope["branch_id"] not in actor.branch_ids
        or scope["warehouse_id"] not in actor.warehouse_ids
    ):
        raise AppError(
            403, "operational_scope_required", "Branch and Warehouse scope are required."
        )
    return await _response(session, return_request_id)


@router.get(
    "/v1/return-requests",
    response_model=ReturnRequestList,
    responses=error_responses(401, 403, 500),
)
async def list_return_requests(
    actor: Annotated[AuthorizedUser, Depends(require_return_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    status: Annotated[Literal["pending_authorization", "authorized"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReturnRequestList:
    query = (
        select(return_requests.c.return_request_id)
        .outerjoin(
            return_authorizations,
            return_authorizations.c.return_request_id == return_requests.c.return_request_id,
        )
        .where(
            return_requests.c.branch_id.in_(actor.branch_ids),
            return_requests.c.warehouse_id.in_(actor.warehouse_ids),
        )
    )
    if status == "pending_authorization":
        query = query.where(return_authorizations.c.return_request_id.is_(None))
    elif status == "authorized":
        query = query.where(return_authorizations.c.return_request_id.is_not(None))
    total = cast(int, await session.scalar(select(func.count()).select_from(query.subquery())))
    ids = list(
        await session.scalars(
            query.order_by(return_requests.c.requested_at.desc()).limit(limit).offset(offset)
        )
    )
    return ReturnRequestList(items=await _responses(session, ids), total=total)


@router.post(
    "/v1/return-requests/{return_request_id}/authorization",
    response_model=ReturnRequestResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def authorize_return_request(
    return_request_id: UUID,
    command: AuthorizeReturnRequest,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_return_authorizer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200, pattern=r".*\S.*"),
    ] = None,
) -> ReturnRequestResponse:
    if not idempotency_key:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _authorization_hash(return_request_id, actor.subject, command)
    await session.rollback()
    async with session.begin():
        source = (
            (
                await session.execute(
                    select(
                        return_requests.c.delivery_receipt_id,
                        return_requests.c.branch_id,
                        return_requests.c.warehouse_id,
                    ).where(return_requests.c.return_request_id == return_request_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if source is None:
            raise AppError(404, "return_request_not_found", "Return Request does not exist.")
        if (
            source["branch_id"] not in actor.branch_ids
            or source["warehouse_id"] not in actor.warehouse_ids
        ):
            raise AppError(
                403, "operational_scope_required", "Branch and Warehouse scope are required."
            )
        await _lock(session, f"delivery-receipt-chain:{source['delivery_receipt_id']}")
        row = (
            (
                await session.execute(
                    select(return_requests)
                    .where(return_requests.c.return_request_id == return_request_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return ReturnRequestResponse.model_validate(replay)
        if command.expected_request_version != 1:
            raise AppError(
                409, "return_request_version_conflict", "Return Request changed; refresh."
            )
        if row["requested_by"] == actor.subject:
            raise AppError(
                403,
                "maker_checker_violation",
                "The requester cannot authorize the same Return Request.",
            )
        if (
            await session.scalar(
                select(return_authorizations.c.return_request_id).where(
                    return_authorizations.c.return_request_id == return_request_id
                )
            )
            is not None
        ):
            raise AppError(
                409, "return_request_already_authorized", "Return Request is already authorized."
            )
        authority = (
            (
                await session.execute(
                    select(approval_authorities)
                    .where(
                        approval_authorities.c.user_subject == actor.subject,
                        approval_authorities.c.capability_code == "returns:authorize",
                        approval_authorities.c.branch_id == row["branch_id"],
                        or_(
                            approval_authorities.c.warehouse_id.is_(None),
                            approval_authorities.c.warehouse_id == row["warehouse_id"],
                        ),
                        or_(
                            approval_authorities.c.maximum_amount.is_(None),
                            approval_authorities.c.maximum_amount
                            >= row["affected_value_base_currency"],
                        ),
                    )
                    .order_by(
                        approval_authorities.c.warehouse_id.is_not(None).desc(),
                        approval_authorities.c.maximum_amount.desc().nulls_first(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if authority is None:
            raise AppError(
                403,
                "approval_authority_required",
                "Sufficient Return Authorization Approval Authority is required.",
            )
        receipt = await _receipt(session, row["delivery_receipt_id"], actor)
        await _assert_current_receipt(session, receipt)
        authorized_by_line = await _authorized_by_line(session, row["delivery_receipt_id"])
        lines = list(
            (
                await session.execute(
                    select(return_request_lines).where(
                        return_request_lines.c.return_request_id == return_request_id
                    )
                )
            ).mappings()
        )
        if any(
            line["quantity_base"]
            > line["delivered_quantity_base"]
            - authorized_by_line.get(line["delivery_line_id"], ZERO)
            for line in lines
        ):
            raise AppError(
                409,
                "return_quantity_exceeds_eligible",
                "Return Authorization quantity exceeds remaining Delivered Quantity.",
            )
        await session.execute(
            insert(return_authorizations).values(
                return_request_id=return_request_id,
                authorized_by=actor.subject,
                approval_authority_id=authority["approval_authority_id"],
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        )
        result = await _response(session, return_request_id)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        response.headers["X-Idempotency-Replayed"] = "false"
    return result


UPLOAD_PART_SIZE = 5 * 1024 * 1024


async def _authorize_return_request_scope(
    session: AsyncSession,
    return_request_id: UUID,
    actor: AuthorizedUser,
) -> Mapping[str, Any]:
    row = (
        (
            await session.execute(
                select(
                    return_requests.c.return_request_id,
                    return_requests.c.branch_id,
                    return_requests.c.warehouse_id,
                ).where(return_requests.c.return_request_id == return_request_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "return_request_not_found", "Return Request does not exist.")
    if row["branch_id"] not in actor.branch_ids or row["warehouse_id"] not in actor.warehouse_ids:
        raise AppError(
            403, "operational_scope_required", "Branch and Warehouse scope are required."
        )
    return cast(Mapping[str, Any], row)


async def _return_request_version(session: AsyncSession, return_request_id: UUID) -> int:
    authorized = await session.scalar(
        select(return_authorizations.c.return_request_id).where(
            return_authorizations.c.return_request_id == return_request_id
        )
    )
    return 2 if authorized is not None else 1


async def _record_sync_state(
    session: AsyncSession,
    *,
    return_request_id: UUID,
    expected_version: int,
    correlation_id: str,
    status: Literal["acknowledged", "conflict", "pending"],
    conflict_reason: str | None = None,
) -> None:
    acknowledged_at: datetime | None = None
    conflict_detected_at: datetime | None = None
    reason: str | None = None
    if status == "acknowledged":
        acknowledged_at = datetime.now(UTC)
    elif status == "conflict":
        conflict_detected_at = datetime.now(UTC)
        reason = conflict_reason
    await session.execute(
        pg_insert(return_request_evidence_sync_state)
        .values(
            return_request_id=return_request_id,
            expected_version=expected_version,
            acknowledged_at=acknowledged_at,
            conflict_detected_at=conflict_detected_at,
            conflict_reason=reason,
            correlation_id=correlation_id,
        )
        .on_conflict_do_update(
            index_elements=[return_request_evidence_sync_state.c.return_request_id],
            set_={
                "expected_version": expected_version,
                "acknowledged_at": acknowledged_at,
                "conflict_detected_at": conflict_detected_at,
                "conflict_reason": reason,
                "correlation_id": correlation_id,
                "updated_at": func.now(),
            },
        )
    )


def _evidence_object_key(return_request_id: UUID, evidence_id: UUID) -> str:
    return f"return-requests/{return_request_id}/evidence/{evidence_id}"


async def _stored_evidence_matches(storage: ObjectStorage, evidence: Mapping[str, Any]) -> bool:
    try:
        stored = await storage.head(evidence["object_key"])
        computed = await storage.computed_sha256(evidence["object_key"])
    except Exception:
        return False
    return bool(
        stored.content_type == evidence["content_type"]
        and stored.size_bytes == evidence["size_bytes"]
        and stored.sha256 == evidence["sha256"]
        and computed == evidence["sha256"]
    )


def _validate_uploaded_parts(parts: list[UploadedPart], size_bytes: int) -> None:
    if not parts:
        raise AppError(
            409,
            "evidence_upload_incomplete",
            "Uploaded evidence is incomplete; no parts were found.",
        )
    total = sum(part.size_bytes for part in parts)
    if total != size_bytes:
        raise AppError(
            409,
            "evidence_upload_size_conflict",
            "Uploaded evidence size does not match the declared intent.",
        )
    seen: set[int] = set()
    for part in parts:
        if part.number in seen:
            raise AppError(
                409,
                "evidence_upload_part_duplicate",
                "Uploaded evidence contains duplicate part numbers.",
            )
        seen.add(part.number)


@router.post(
    "/v1/return-requests/{return_request_id}/evidence/uploads",
    response_model=EvidenceUploadResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def create_return_evidence_upload(
    return_request_id: UUID,
    command: ReturnEvidenceUploadIntent,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_return_evidence_capturer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> EvidenceUploadResponse:
    await session.rollback()
    async with session.begin():
        await _authorize_return_request_scope(session, return_request_id, actor)
        await _lock(session, f"return-request-evidence:{return_request_id}")
        existing = (
            (
                await session.execute(
                    select(return_request_evidence).where(
                        return_request_evidence.c.evidence_id == command.evidence_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        object_key = _evidence_object_key(return_request_id, command.evidence_id)
        if existing is not None:
            same = (
                existing["return_request_id"] == return_request_id
                and existing["captured_by"] == actor.subject
                and existing["kind"] == command.kind
                and existing["content_type"] == command.content_type
                and existing["size_bytes"] == command.size_bytes
                and existing["sha256"] == command.sha256
                and existing["device_captured_at"] == command.device_captured_at
            )
            if not same:
                raise AppError(
                    409,
                    "return_evidence_identity_conflict",
                    "Evidence identity was already used for different proof.",
                )
            if existing["status"] == "verified":
                response.status_code = 200
                return EvidenceUploadResponse(
                    evidence_id=command.evidence_id,
                    status="verified",
                    upload_id=None,
                    part_size=None,
                    parts=[],
                    expires_at=None,
                )
            response.status_code = 200
        try:
            await storage.ensure_bucket()
            upload_id = (
                cast(str, existing["upload_id"])
                if existing is not None
                else await storage.create_multipart_upload(
                    content_type=command.content_type,
                    object_key=object_key,
                    sha256=command.sha256,
                )
            )
            uploaded_parts = await storage.list_uploaded_parts(
                object_key=object_key,
                upload_id=upload_id,
            )
        except Exception as error:
            if existing is not None and await _stored_evidence_matches(
                storage, cast(Mapping[str, Any], existing)
            ):
                await session.execute(
                    update(return_request_evidence)
                    .where(return_request_evidence.c.evidence_id == command.evidence_id)
                    .values(status="verified", verified_at=func.now())
                )
                response.status_code = 200
                return EvidenceUploadResponse(
                    evidence_id=command.evidence_id,
                    status="verified",
                    upload_id=None,
                    part_size=None,
                    parts=[],
                    expires_at=None,
                )
            raise AppError(
                503,
                "evidence_storage_unavailable",
                "Evidence storage could not prepare the private resumable upload.",
            ) from error
        if existing is None:
            await session.execute(
                insert(return_request_evidence).values(
                    evidence_id=command.evidence_id,
                    return_request_id=return_request_id,
                    kind=command.kind,
                    object_key=object_key,
                    content_type=command.content_type,
                    size_bytes=command.size_bytes,
                    sha256=command.sha256,
                    upload_id=upload_id,
                    captured_by=actor.subject,
                    device_captured_at=command.device_captured_at,
                    status="uploading",
                )
            )
        completed_numbers = {part.number for part in uploaded_parts}
        part_count = (command.size_bytes + UPLOAD_PART_SIZE - 1) // UPLOAD_PART_SIZE
        return EvidenceUploadResponse(
            evidence_id=command.evidence_id,
            status="uploading",
            upload_id=upload_id,
            part_size=UPLOAD_PART_SIZE,
            parts=[
                EvidenceUploadPartResponse(
                    part_number=part_number,
                    start_byte=(part_number - 1) * UPLOAD_PART_SIZE,
                    end_byte=min(part_number * UPLOAD_PART_SIZE, command.size_bytes),
                    upload_url=storage.signed_upload_part_url(
                        object_key=object_key,
                        part_number=part_number,
                        upload_id=upload_id,
                    ),
                    upload_headers={},
                )
                for part_number in range(1, part_count + 1)
                if part_number not in completed_numbers
            ],
            expires_at=datetime.now(UTC) + timedelta(seconds=storage.url_expiry_seconds),
        )


@router.post(
    "/v1/return-requests/{return_request_id}/evidence/{evidence_id}/complete",
    response_model=EvidenceUploadResponse,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def complete_return_evidence_upload(
    return_request_id: UUID,
    evidence_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_return_evidence_capturer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> EvidenceUploadResponse:
    await session.rollback()
    async with session.begin():
        await _authorize_return_request_scope(session, return_request_id, actor)
        await _lock(session, f"return-request-evidence:{return_request_id}")
        evidence = (
            (
                await session.execute(
                    select(return_request_evidence)
                    .where(
                        return_request_evidence.c.evidence_id == evidence_id,
                        return_request_evidence.c.return_request_id == return_request_id,
                        return_request_evidence.c.captured_by == actor.subject,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if evidence is None:
            raise AppError(404, "return_evidence_not_found", "Evidence does not exist.")
        if evidence["status"] != "verified":
            try:
                if not await _stored_evidence_matches(storage, cast(Mapping[str, Any], evidence)):
                    parts = await storage.list_uploaded_parts(
                        object_key=evidence["object_key"],
                        upload_id=evidence["upload_id"],
                    )
                    _validate_uploaded_parts(parts, evidence["size_bytes"])
                    await storage.complete_multipart_upload(
                        object_key=evidence["object_key"],
                        parts=parts,
                        upload_id=evidence["upload_id"],
                    )
            except AppError:
                raise
            except Exception as error:
                raise AppError(
                    503,
                    "evidence_storage_unavailable",
                    "Evidence storage could not verify the uploaded object.",
                ) from error
            if not await _stored_evidence_matches(storage, cast(Mapping[str, Any], evidence)):
                raise AppError(
                    409,
                    "return_evidence_integrity_conflict",
                    "Uploaded evidence type, size, or SHA-256 did not match the intent.",
                )
            await session.execute(
                update(return_request_evidence)
                .where(return_request_evidence.c.evidence_id == evidence_id)
                .values(status="verified", verified_at=func.now())
            )
        return EvidenceUploadResponse(
            evidence_id=evidence_id,
            status="verified",
            upload_id=None,
            part_size=None,
            parts=[],
            expires_at=None,
        )


@router.post(
    "/v1/return-requests/{return_request_id}/evidence/notes",
    response_model=ReturnEvidenceItem,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def create_return_evidence_note(
    return_request_id: UUID,
    command: ReturnEvidenceNoteCommand,
    actor: Annotated[AuthorizedUser, Depends(require_return_evidence_capturer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReturnEvidenceItem:
    await session.rollback()
    async with session.begin():
        await _authorize_return_request_scope(session, return_request_id, actor)
        await _lock(session, f"return-request-evidence:{return_request_id}")
        existing = await session.scalar(
            select(return_request_evidence.c.evidence_id).where(
                return_request_evidence.c.evidence_id == command.evidence_id
            )
        )
        if existing is not None:
            raise AppError(
                409,
                "return_evidence_identity_conflict",
                "Evidence identity was already used for different proof.",
            )
        await session.execute(
            insert(return_request_evidence).values(
                evidence_id=command.evidence_id,
                return_request_id=return_request_id,
                kind="note",
                note_text=command.note_text,
                captured_by=actor.subject,
                device_captured_at=command.device_captured_at,
                status="verified",
                verified_at=func.now(),
            )
        )
        return ReturnEvidenceItem(
            evidence_id=command.evidence_id,
            kind="note",
            status="verified",
            content_type=None,
            size_bytes=None,
            sha256=None,
            note_text=command.note_text,
            captured_by=actor.subject,
            device_captured_at=command.device_captured_at,
            created_at=datetime.now(UTC),
            verified_at=datetime.now(UTC),
        )


@router.post(
    "/v1/return-requests/{return_request_id}/evidence/{evidence_id}/access",
    response_model=SignedAccessResponse,
    responses=error_responses(401, 403, 404, 409, 500),
)
async def access_return_evidence(
    return_request_id: UUID,
    evidence_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_return_evidence_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> SignedAccessResponse:
    await _authorize_return_request_scope(session, return_request_id, actor)
    evidence = (
        (
            await session.execute(
                select(return_request_evidence).where(
                    return_request_evidence.c.evidence_id == evidence_id,
                    return_request_evidence.c.return_request_id == return_request_id,
                    return_request_evidence.c.status == "verified",
                    return_request_evidence.c.kind == "photo",
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if evidence is None:
        raise AppError(404, "return_evidence_not_found", "Verified photo evidence does not exist.")
    return SignedAccessResponse(
        access_url=storage.signed_get_url(object_key=evidence["object_key"]),
        expires_at=datetime.now(UTC) + timedelta(seconds=storage.url_expiry_seconds),
    )


@router.get(
    "/v1/return-requests/{return_request_id}/evidence",
    response_model=ReturnEvidenceList,
    responses=error_responses(401, 403, 404, 500),
)
async def list_return_evidence(
    return_request_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_return_evidence_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReturnEvidenceList:
    await _authorize_return_request_scope(session, return_request_id, actor)
    rows = list(
        (
            await session.execute(
                select(return_request_evidence).where(
                    return_request_evidence.c.return_request_id == return_request_id
                )
            )
        ).mappings()
    )
    return ReturnEvidenceList(
        items=[
            ReturnEvidenceItem(
                evidence_id=row["evidence_id"],
                kind=row["kind"],
                status=row["status"],
                content_type=row["content_type"],
                size_bytes=row["size_bytes"],
                sha256=row["sha256"],
                note_text=row["note_text"],
                captured_by=row["captured_by"],
                device_captured_at=row["device_captured_at"],
                created_at=row["created_at"],
                verified_at=row["verified_at"],
            )
            for row in rows
        ]
    )


@router.post(
    "/v1/return-requests/{return_request_id}/offline-evidence",
    response_model=ReturnEvidenceSyncState,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def sync_offline_return_evidence(
    return_request_id: UUID,
    command: SyncOfflineEvidenceCommand,
    request: Request,
    actor: Annotated[AuthorizedUser, Depends(require_return_evidence_capturer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReturnEvidenceSyncState:
    await session.rollback()
    async with session.begin():
        await _authorize_return_request_scope(session, return_request_id, actor)
        await _lock(session, f"return-request-evidence:{return_request_id}")
        current_version = await _return_request_version(session, return_request_id)
        if current_version != command.expected_request_version:
            conflict_reason = (
                f"Return Request changed from version {command.expected_request_version} "
                f"to version {current_version} before the offline evidence could be synced."
            )
            await _record_sync_state(
                session,
                return_request_id=return_request_id,
                expected_version=command.expected_request_version,
                correlation_id=command.correlation_id,
                status="conflict",
                conflict_reason=conflict_reason,
            )
            return ReturnEvidenceSyncState(
                return_request_id=return_request_id,
                expected_version=command.expected_request_version,
                current_version=current_version,
                status="conflict",
                acknowledged_at=None,
                conflict_detected_at=datetime.now(UTC),
                conflict_reason=conflict_reason,
            )
        evidence_ids = [entry.evidence_id for entry in command.evidence]
        existing = (
            await session.scalars(
                select(return_request_evidence.c.evidence_id).where(
                    return_request_evidence.c.evidence_id.in_(evidence_ids)
                )
            )
        ).all()
        existing_set = set(existing)
        for entry in command.evidence:
            if entry.evidence_id in existing_set:
                continue
            if entry.kind == "photo":
                await session.execute(
                    insert(return_request_evidence).values(
                        evidence_id=entry.evidence_id,
                        return_request_id=return_request_id,
                        kind="photo",
                        captured_by=actor.subject,
                        device_captured_at=datetime.now(UTC),
                        status="uploading",
                        sync_correlation_id=command.correlation_id,
                    )
                )
            elif entry.kind == "note":
                if not entry.note_text:
                    raise AppError(
                        422,
                        "return_evidence_note_text_required",
                        "Note evidence must include note_text.",
                    )
                await session.execute(
                    insert(return_request_evidence).values(
                        evidence_id=entry.evidence_id,
                        return_request_id=return_request_id,
                        kind="note",
                        note_text=entry.note_text,
                        captured_by=actor.subject,
                        device_captured_at=datetime.now(UTC),
                        status="verified",
                        verified_at=func.now(),
                        sync_correlation_id=command.correlation_id,
                    )
                )
        await _record_sync_state(
            session,
            return_request_id=return_request_id,
            expected_version=command.expected_request_version,
            correlation_id=command.correlation_id,
            status="acknowledged",
        )
        return ReturnEvidenceSyncState(
            return_request_id=return_request_id,
            expected_version=command.expected_request_version,
            current_version=current_version,
            status="acknowledged",
            acknowledged_at=datetime.now(UTC),
            conflict_detected_at=None,
            conflict_reason=None,
        )


@router.get(
    "/v1/return-requests/{return_request_id}/sync-state",
    response_model=ReturnEvidenceSyncState,
    responses=error_responses(401, 403, 404, 500),
)
async def get_return_evidence_sync_state(
    return_request_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_return_evidence_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ReturnEvidenceSyncState:
    await _authorize_return_request_scope(session, return_request_id, actor)
    current_version = await _return_request_version(session, return_request_id)
    state = (
        (
            await session.execute(
                select(return_request_evidence_sync_state).where(
                    return_request_evidence_sync_state.c.return_request_id == return_request_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        return ReturnEvidenceSyncState(
            return_request_id=return_request_id,
            expected_version=current_version,
            current_version=current_version,
            status="pending",
            acknowledged_at=None,
            conflict_detected_at=None,
            conflict_reason=None,
        )
    if state["acknowledged_at"] is not None:
        status: Literal["acknowledged", "conflict", "pending"] = "acknowledged"
    elif state["conflict_detected_at"] is not None:
        status = "conflict"
    else:
        status = "pending"
    return ReturnEvidenceSyncState(
        return_request_id=return_request_id,
        expected_version=state["expected_version"],
        current_version=current_version,
        status=status,
        acknowledged_at=state["acknowledged_at"],
        conflict_detected_at=state["conflict_detected_at"],
        conflict_reason=state["conflict_reason"],
    )
