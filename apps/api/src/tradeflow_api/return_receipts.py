from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import AuthorizedUser, require_return_receiver
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.delivery_confirmation import (
    UPLOAD_PART_SIZE,
    EvidenceUploadIntent,
    EvidenceUploadPartResponse,
    EvidenceUploadResponse,
    SignedAccessResponse,
    _stored_evidence_matches,
    _validate_uploaded_parts,
)
from tradeflow_api.delivery_partitioning import ensure_custody_location
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.inventory_projection_service import (
    apply_availability_delta,
    apply_valuation_delta,
)
from tradeflow_api.models import (
    approval_authorities,
    inventory_valuation,
    return_authorizations,
    return_receipt_evidence,
    return_receipt_lines,
    return_receipts,
    return_request_evidence,
    return_request_lines,
    return_requests,
    stock_movements,
)
from tradeflow_api.object_storage import ObjectStorage, get_object_storage

router = APIRouter(tags=["return-receipts"])
ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReturnReceiptLineCommand(CommandModel):
    return_request_line_id: UUID
    received_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    outcome: Literal["restock", "quarantine", "damaged", "rejected"]
    notes: Annotated[str | None, Field(max_length=2000)] = None


class CreateReturnReceipt(CommandModel):
    return_receipt_id: UUID
    expected_request_version: int = Field(gt=0)
    received_at: datetime
    notes: Annotated[str | None, Field(max_length=2000)] = None
    evidence_ids: list[UUID] = Field(default_factory=list)
    lines: list[ReturnReceiptLineCommand] = Field(min_length=1)


class ReturnReceiptLineResponse(BaseModel):
    return_request_line_id: UUID
    delivery_line_id: UUID
    line_id: UUID
    sku_id: UUID
    received_quantity_base: Decimal
    outcome: Literal["restock", "quarantine", "damaged", "rejected"]
    movement_id: UUID | None
    custody: Literal["available", "quarantine", None]


class ReturnReceiptResponse(BaseModel):
    return_receipt_id: UUID
    return_request_id: UUID
    status: Literal["received"]
    version: int
    received_by: str
    received_at: datetime
    notes: str | None
    lines: list[ReturnReceiptLineResponse]


def _request_hash(request_id: UUID, actor: str, command: BaseModel) -> str:
    raw = f"return-receipt:{request_id}:{actor}:{command.model_dump_json()}"
    return sha256(raw.encode()).hexdigest()


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


async def _load_authorized_return_request(
    session: AsyncSession,
    return_request_id: UUID,
    actor: AuthorizedUser,
    *,
    for_update: bool = False,
) -> Mapping[str, Any]:
    query = (
        select(
            return_requests,
            return_authorizations.c.authorized_by,
            return_authorizations.c.authorized_at,
            return_receipts.c.return_receipt_id.label("existing_receipt_id"),
        )
        .outerjoin(
            return_authorizations,
            return_authorizations.c.return_request_id == return_requests.c.return_request_id,
        )
        .outerjoin(
            return_receipts,
            return_receipts.c.return_request_id == return_requests.c.return_request_id,
        )
        .where(return_requests.c.return_request_id == return_request_id)
    )
    if for_update:
        query = query.with_for_update(of=return_requests)
    row = (await session.execute(query)).mappings().one_or_none()
    if row is None:
        raise AppError(404, "return_request_not_found", "Return Request does not exist.")
    if row["branch_id"] not in actor.branch_ids or row["warehouse_id"] not in actor.warehouse_ids:
        raise AppError(
            403, "operational_scope_required", "Branch and Warehouse scope are required."
        )
    if row["authorized_by"] is None:
        raise AppError(409, "return_request_not_authorized", "Return Request is not authorized.")
    if row["existing_receipt_id"] is not None:
        raise AppError(
            409,
            "return_request_already_receipted",
            "Return Request has already been receipted.",
        )
    return cast(Mapping[str, Any], row)


async def _validate_approval_authority(
    session: AsyncSession, request: Mapping[str, Any], actor: AuthorizedUser
) -> Mapping[str, Any]:
    authority = (
        (
            await session.execute(
                select(approval_authorities)
                .where(
                    approval_authorities.c.user_subject == actor.subject,
                    approval_authorities.c.capability_code == "returns:receive",
                    approval_authorities.c.branch_id == request["branch_id"],
                    or_(
                        approval_authorities.c.warehouse_id.is_(None),
                        approval_authorities.c.warehouse_id == request["warehouse_id"],
                    ),
                    or_(
                        approval_authorities.c.maximum_amount.is_(None),
                        approval_authorities.c.maximum_amount
                        >= request["affected_value_base_currency"],
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
            "Sufficient Return Receipt Approval Authority is required.",
        )
    return cast(Mapping[str, Any], authority)


async def _verified_evidence(
    session: AsyncSession,
    return_request_id: UUID,
    evidence_ids: list[UUID],
    actor_subject: str,
) -> None:
    if not evidence_ids:
        raise AppError(
            409,
            "return_evidence_conflict",
            "At least one verified photo is required for a Return Receipt.",
        )
    if len(set(evidence_ids)) != len(evidence_ids):
        raise AppError(
            409, "return_evidence_conflict", "Evidence IDs must be unique within the receipt."
        )
    rows = list(
        (
            await session.execute(
                select(return_request_evidence).where(
                    return_request_evidence.c.return_request_id == return_request_id,
                    return_request_evidence.c.evidence_id.in_(evidence_ids),
                )
            )
        ).mappings()
    )
    if len(rows) != len(evidence_ids):
        raise AppError(
            409,
            "return_evidence_conflict",
            "All evidence must belong to the Return Request.",
        )
    for row in rows:
        if row["captured_by"] != actor_subject:
            raise AppError(
                409,
                "return_evidence_conflict",
                "Evidence must be captured by the receiving actor.",
            )
        if row["status"] != "verified":
            raise AppError(
                409,
                "return_evidence_conflict",
                "All evidence must be verified before posting the receipt.",
            )


@router.post(
    "/v1/return-requests/{return_request_id}/evidence/uploads",
    response_model=EvidenceUploadResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def create_return_evidence_upload(
    return_request_id: UUID,
    command: EvidenceUploadIntent,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_return_receiver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> EvidenceUploadResponse:
    await session.rollback()
    async with session.begin():
        await _lock(session, f"return-request-receipt:{return_request_id}")
        request = await _load_authorized_return_request(
            session, return_request_id, actor, for_update=False
        )
        if request["requested_by"] == actor.subject:
            raise AppError(
                403,
                "maker_checker_violation",
                "The requester cannot receive the same Return Request.",
            )
        if command.kind != "photo":
            raise AppError(
                422,
                "request_validation_failed",
                "Return evidence kind must be photo.",
            )
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
        object_key = f"return-requests/{return_request_id}/evidence/{command.evidence_id}"
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
    actor: Annotated[AuthorizedUser, Depends(require_return_receiver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> EvidenceUploadResponse:
    await session.rollback()
    async with session.begin():
        await _lock(session, f"return-request-receipt:{return_request_id}")
        await _load_authorized_return_request(session, return_request_id, actor, for_update=False)
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
    "/v1/return-requests/{return_request_id}/evidence/{evidence_id}/access",
    response_model=SignedAccessResponse,
    responses=error_responses(401, 403, 404, 409, 503),
)
async def access_return_evidence(
    return_request_id: UUID,
    evidence_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_return_receiver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> SignedAccessResponse:
    await _load_authorized_return_request(session, return_request_id, actor, for_update=False)
    object_key = await session.scalar(
        select(return_request_evidence.c.object_key).where(
            return_request_evidence.c.evidence_id == evidence_id,
            return_request_evidence.c.return_request_id == return_request_id,
            return_request_evidence.c.status == "verified",
        )
    )
    if object_key is None:
        raise AppError(
            404,
            "return_evidence_not_found",
            "Verified Return evidence does not exist.",
        )
    return SignedAccessResponse(
        access_url=storage.signed_get_url(object_key=object_key),
        expires_at=datetime.now(UTC) + timedelta(seconds=storage.url_expiry_seconds),
    )


async def _current_moving_average_unit_cost(
    session: AsyncSession, sku_id: UUID, warehouse_id: UUID
) -> Decimal:
    row = (
        (
            await session.execute(
                select(inventory_valuation.c.moving_average_unit_cost)
                .where(
                    inventory_valuation.c.sku_id == sku_id,
                    inventory_valuation.c.warehouse_id == warehouse_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(
            409,
            "inventory_valuation_missing",
            "SKU valuation is not initialized for the warehouse.",
        )
    return cast(Decimal, row["moving_average_unit_cost"])


async def _create_movement(
    session: AsyncSession,
    *,
    return_receipt_id: UUID,
    return_request_line_id: UUID,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    unit_cost: Decimal,
    value_delta: Decimal,
    movement_leg: Literal["authorized_return_available_in", "authorized_return_quarantine_in"],
    base_currency: str,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
) -> UUID:
    movement_id = uuid4()
    await session.execute(
        insert(stock_movements).values(
            movement_id=movement_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            location_id=location_id,
            movement_type="authorized_return_receipt",
            movement_leg=movement_leg,
            quantity_base=quantity,
            unit_cost=unit_cost,
            value_delta=value_delta,
            base_currency=base_currency,
            source_reference=f"RETURN-RECEIPT:{return_receipt_id}",
            entered_unit="base",
            conversion_snapshot={},
            actor_subject=actor_subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            movement_group_id=return_receipt_id,
        )
    )
    return movement_id


@router.post(
    "/v1/return-requests/{return_request_id}/receipts",
    response_model=ReturnReceiptResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def create_return_receipt(
    return_request_id: UUID,
    command: CreateReturnReceipt,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_return_receiver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200, pattern=r".*\S.*"),
    ] = None,
) -> ReturnReceiptResponse:
    if not idempotency_key:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash(return_request_id, actor.subject, command)
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return ReturnReceiptResponse.model_validate(replay)
        await _lock(session, f"return-request-receipt:{return_request_id}")
        receipt_request = await _load_authorized_return_request(
            session, return_request_id, actor, for_update=True
        )
        if receipt_request["requested_by"] == actor.subject:
            raise AppError(
                403,
                "maker_checker_violation",
                "The requester cannot receive the same Return Request.",
            )
        if command.expected_request_version != 2:
            raise AppError(
                409, "return_request_version_conflict", "Return Request changed; refresh."
            )
        await _validate_approval_authority(session, receipt_request, actor)
        await _verified_evidence(
            session,
            return_request_id,
            command.evidence_ids,
            actor.subject,
        )

        request_lines = list(
            (
                await session.execute(
                    select(return_request_lines).where(
                        return_request_lines.c.return_request_id == return_request_id
                    )
                )
            ).mappings()
        )
        line_by_id = {line["return_request_line_id"]: line for line in request_lines}
        supplied = {line.return_request_line_id: line for line in command.lines}
        if len(supplied) != len(command.lines):
            raise AppError(
                409,
                "return_receipt_line_conflict",
                "Each Return Request line may appear once.",
            )
        if not set(supplied).issubset(line_by_id):
            raise AppError(
                409,
                "return_receipt_line_conflict",
                "Every receipt line must belong to the Return Request.",
            )

        available_location_id = await ensure_custody_location(
            session,
            warehouse_id=receipt_request["warehouse_id"],
            custody="available",
            actor_subject=actor.subject,
        )
        quarantine_location_id = await ensure_custody_location(
            session,
            warehouse_id=receipt_request["warehouse_id"],
            custody="quarantine",
            actor_subject=actor.subject,
        )

        await session.execute(
            insert(return_receipts).values(
                return_receipt_id=command.return_receipt_id,
                return_request_id=return_request_id,
                received_by=actor.subject,
                received_at=command.received_at,
                notes=command.notes,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )

        result_lines: list[ReturnReceiptLineResponse] = []
        for line_command in command.lines:
            source_line = line_by_id[line_command.return_request_line_id]
            if line_command.received_quantity_base > source_line["quantity_base"]:
                raise AppError(
                    409,
                    "return_quantity_exceeds_authorized",
                    "Received quantity exceeds authorized Return Request quantity.",
                )

            if line_command.outcome == "rejected":
                await session.execute(
                    insert(return_receipt_lines).values(
                        return_receipt_line_id=uuid4(),
                        return_receipt_id=command.return_receipt_id,
                        return_request_line_id=line_command.return_request_line_id,
                        received_quantity_base=ZERO,
                        outcome="rejected",
                        notes=line_command.notes,
                        movement_id=None,
                    )
                )
                result_lines.append(
                    ReturnReceiptLineResponse(
                        return_request_line_id=line_command.return_request_line_id,
                        delivery_line_id=source_line["delivery_line_id"],
                        line_id=source_line["line_id"],
                        sku_id=source_line["sku_id"],
                        received_quantity_base=ZERO,
                        outcome="rejected",
                        movement_id=None,
                        custody=None,
                    )
                )
                continue

            target_location_id = (
                available_location_id
                if line_command.outcome == "restock"
                else quarantine_location_id
            )
            custody: Literal["available", "quarantine"] = (
                "available" if line_command.outcome == "restock" else "quarantine"
            )
            movement_leg: Literal[
                "authorized_return_available_in", "authorized_return_quarantine_in"
            ] = (
                "authorized_return_available_in"
                if line_command.outcome == "restock"
                else "authorized_return_quarantine_in"
            )

            unit_cost = ZERO
            value_delta = ZERO
            if line_command.outcome == "restock":
                unit_cost = await _current_moving_average_unit_cost(
                    session, source_line["sku_id"], receipt_request["warehouse_id"]
                )
                value_delta = (unit_cost * line_command.received_quantity_base).quantize(
                    SIX_PLACES, ROUND_HALF_UP
                )

            movement_id = await _create_movement(
                session,
                return_receipt_id=command.return_receipt_id,
                return_request_line_id=line_command.return_request_line_id,
                sku_id=source_line["sku_id"],
                warehouse_id=receipt_request["warehouse_id"],
                location_id=target_location_id,
                quantity=line_command.received_quantity_base,
                unit_cost=unit_cost,
                value_delta=value_delta,
                movement_leg=movement_leg,
                base_currency=receipt_request["base_currency"],
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=f"{idempotency_key}:{line_command.return_request_line_id}",
            )

            await apply_availability_delta(
                session,
                sku_id=source_line["sku_id"],
                warehouse_id=receipt_request["warehouse_id"],
                location_id=target_location_id,
                quantity=line_command.received_quantity_base,
                identity=None,
                conflict_code="inventory_projection_conflict",
                conflict_message="Unable to apply return receipt availability delta.",
            )

            if line_command.outcome == "restock":
                await apply_valuation_delta(
                    session,
                    sku_id=source_line["sku_id"],
                    warehouse_id=receipt_request["warehouse_id"],
                    quantity_delta=line_command.received_quantity_base,
                    value_delta=value_delta,
                    allow_create=True,
                )

            await session.execute(
                insert(return_receipt_lines).values(
                    return_receipt_line_id=uuid4(),
                    return_receipt_id=command.return_receipt_id,
                    return_request_line_id=line_command.return_request_line_id,
                    received_quantity_base=line_command.received_quantity_base,
                    outcome=line_command.outcome,
                    notes=line_command.notes,
                    movement_id=movement_id,
                )
            )
            result_lines.append(
                ReturnReceiptLineResponse(
                    return_request_line_id=line_command.return_request_line_id,
                    delivery_line_id=source_line["delivery_line_id"],
                    line_id=source_line["line_id"],
                    sku_id=source_line["sku_id"],
                    received_quantity_base=line_command.received_quantity_base,
                    outcome=line_command.outcome,
                    movement_id=movement_id,
                    custody=custody,
                )
            )

        if command.evidence_ids:
            await session.execute(
                insert(return_receipt_evidence),
                [
                    {"return_receipt_id": command.return_receipt_id, "evidence_id": evidence_id}
                    for evidence_id in command.evidence_ids
                ],
            )

        result = ReturnReceiptResponse(
            return_receipt_id=command.return_receipt_id,
            return_request_id=return_request_id,
            status="received",
            version=command.expected_request_version + 1,
            received_by=actor.subject,
            received_at=command.received_at,
            notes=command.notes,
            lines=result_lines,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        response.headers["X-Idempotency-Replayed"] = "false"
        return result
