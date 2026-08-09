from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import case, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_cod_on_account_converter,
    require_delivery_confirmer,
)
from tradeflow_api.cod_settlement import calculate_cod_amount_due
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.delivery_partitioning import (
    ensure_custody_location,
    post_delivery_partition_line,
    validate_delivery_identity_partitions,
    validate_delivery_partitions,
)
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    branches,
    cash_reconciliation_items,
    cod_collections,
    cod_on_account_conversions,
    commercial_approvals,
    companies,
    credit_exposure_entries,
    customer_accounts,
    customer_credit_exposure,
    delivery_confirmation_evidence,
    delivery_confirmations,
    delivery_dispatches,
    delivery_evidence,
    delivery_lines,
    delivery_receipt_documents,
    delivery_receipts,
    delivery_state,
    document_series,
    document_series_number_audit,
    fulfillment_order_state,
    inventory_availability,
    inventory_valuation,
    outbox_events,
    outbox_processing_state,
    payment_methods,
    payment_receipt_balances,
    payment_receipt_events,
    payment_receipt_status,
    payment_receipts,
    pick_identity_assignments,
    pick_lines,
    sales_order_line_revisions,
    skus,
    warehouse_stock_locations,
)
from tradeflow_api.money import currency_quantum
from tradeflow_api.object_storage import ObjectStorage, UploadedPart, get_object_storage

router = APIRouter(tags=["delivery confirmation"])
ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")
UPLOAD_PART_SIZE = 5 * 1024 * 1024


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmationIdentityPartitionCommand(CommandModel):
    delivery_line_identity_allocation_id: UUID
    accepted_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)
    refused_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)
    damaged_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)
    short_missing_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)
    still_undelivered_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)


class DeliveryExceptionDetailCommand(CommandModel):
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[UUID] = Field(min_length=1)
    responsible_party_type: Literal["staff", "carrier", "customer", "unknown"]
    responsible_subject: str | None = Field(default=None, max_length=200)


class DeliveryExceptionDetailsCommand(CommandModel):
    refused: DeliveryExceptionDetailCommand | None = None
    damaged: DeliveryExceptionDetailCommand | None = None
    short_missing: DeliveryExceptionDetailCommand | None = None
    still_undelivered: DeliveryExceptionDetailCommand | None = None


class ConfirmationLineCommand(CommandModel):
    delivery_line_id: UUID | None = None
    line_id: UUID | None = None
    accepted_quantity_base: Decimal = Field(ge=0)
    refused_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)
    damaged_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)
    short_missing_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)
    still_undelivered_quantity_base: Decimal = Field(default=Decimal("0"), ge=0)
    exception_details: DeliveryExceptionDetailsCommand = Field(
        default_factory=DeliveryExceptionDetailsCommand
    )
    identity_partitions: list[ConfirmationIdentityPartitionCommand] = Field(default_factory=list)

    @model_validator(mode="after")
    def exception_reason_required(self) -> ConfirmationLineCommand:
        if self.delivery_line_id is None and self.line_id is None:
            raise ValueError("delivery_line_id is required.")
        for outcome, quantity in (
            ("refused", self.refused_quantity_base),
            ("damaged", self.damaged_quantity_base),
            ("short_missing", self.short_missing_quantity_base),
            ("still_undelivered", self.still_undelivered_quantity_base),
        ):
            detail = getattr(self.exception_details, outcome)
            if (quantity > 0) != (detail is not None):
                raise ValueError(
                    f"exception_details.{outcome} must be present exactly when "
                    "its quantity is positive."
                )
        return self


class CollectionEvidenceCommand(CommandModel):
    account_or_provider: str = Field(min_length=1, max_length=200)
    reference: str = Field(min_length=1, max_length=200)
    attachment_ids: list[UUID] = Field(default_factory=list)


class CODCollectionCommand(CommandModel):
    payment_receipt_id: UUID
    payment_method: Literal["cash", "bank_transfer", "check", "electronic"]
    amount: Decimal = Field(gt=0, max_digits=24, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    received_at: datetime
    external_reference: str | None = Field(default=None, max_length=200)
    evidence: CollectionEvidenceCommand | None = None


class ConfirmDeliveryCommand(CommandModel):
    confirmation_id: UUID
    expected_delivery_version: int = Field(gt=0)
    recipient_name: str = Field(min_length=1, max_length=300)
    device_captured_at: datetime
    notes: str | None = Field(default=None, max_length=2000)
    evidence_ids: list[UUID] = Field(min_length=1)
    lines: list[ConfirmationLineCommand] = Field(min_length=1)
    collection: CODCollectionCommand | None = None
    on_account_conversion_id: UUID | None = None


class ConfirmationQuoteCommand(CommandModel):
    expected_delivery_version: int = Field(gt=0)
    lines: list[ConfirmationLineCommand] = Field(min_length=1)


class ConfirmationQuoteResponse(BaseModel):
    delivery_id: UUID
    delivery_version: int
    accepted_quantity_base: Decimal
    amount_due: Decimal
    currency: str


class ConvertCODOnAccountCommand(CommandModel):
    conversion_id: UUID
    expected_delivery_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)


class ConfirmationLineResponse(BaseModel):
    delivery_line_id: UUID
    line_id: UUID
    sku_id: UUID
    accepted_quantity_base: Decimal
    refused_quantity_base: Decimal
    damaged_quantity_base: Decimal
    short_missing_quantity_base: Decimal
    still_undelivered_quantity_base: Decimal
    unit_cost: Decimal
    value_delta: Decimal
    outbound_movement_id: UUID | None


class DeliveryReceiptResponse(BaseModel):
    delivery_receipt_id: UUID
    number: str
    status: Literal["pending_document", "ready", "unavailable"]


class CODCollectionResponse(BaseModel):
    payment_receipt_id: UUID
    amount_due: Decimal
    amount_collected: Decimal
    currency: str
    payment_method: str
    status: Literal["cleared", "pending_verification"]
    application_status: Literal["unapplied"]
    cash_reconciliation_status: Literal["pending"] | None


class CODOnAccountConversionResponse(BaseModel):
    conversion_id: UUID
    delivery_id: UUID
    amount: Decimal
    currency: str
    status: Literal["approved", "consumed"]
    approved_by: str


class DeliveryConfirmationResponse(BaseModel):
    confirmation_id: UUID
    delivery_id: UUID
    status: Literal["confirmed"]
    version: int
    lines: list[ConfirmationLineResponse]
    delivery_receipt: DeliveryReceiptResponse | None
    outbox_event_id: UUID | None
    collection: CODCollectionResponse | None = None
    on_account_conversion: CODOnAccountConversionResponse | None = None


class EvidenceUploadIntent(CommandModel):
    evidence_id: UUID
    kind: Literal["signature", "photo"]
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


class DeliveryReceiptDetailResponse(BaseModel):
    delivery_receipt_id: UUID
    delivery_id: UUID
    number: str
    snapshot: dict[str, object]
    status: Literal["pending_document", "ready", "unavailable"]


async def _authorize_assigned_delivery(
    session: AsyncSession,
    *,
    delivery_id: UUID,
    actor: AuthorizedUser,
) -> Mapping[str, Any]:
    delivery = await _delivery(session, delivery_id)
    if delivery is None:
        raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
    if delivery["assigned_to"] != actor.subject:
        raise AppError(
            403,
            "delivery_assignment_required",
            "The Delivery is no longer assigned to this user.",
        )
    if delivery["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "operational_scope_required",
            "Branch Operational Scope is required.",
        )
    return delivery


@router.post(
    "/v1/deliveries/{delivery_id}/evidence/uploads",
    response_model=EvidenceUploadResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def create_evidence_upload(
    delivery_id: UUID,
    command: EvidenceUploadIntent,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> EvidenceUploadResponse:
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        delivery = await _authorize_assigned_delivery(
            session,
            delivery_id=delivery_id,
            actor=actor,
        )
        if delivery["delivery_status"] != "dispatched":
            raise AppError(409, "delivery_version_conflict", "The Delivery is no longer open.")
        existing = (
            (
                await session.execute(
                    select(delivery_evidence).where(
                        delivery_evidence.c.evidence_id == command.evidence_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        object_key = f"deliveries/{delivery_id}/evidence/{command.evidence_id}"
        if existing is not None:
            same = (
                existing["delivery_id"] == delivery_id
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
                    "delivery_evidence_identity_conflict",
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
                    update(delivery_evidence)
                    .where(delivery_evidence.c.evidence_id == command.evidence_id)
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
                insert(delivery_evidence).values(
                    evidence_id=command.evidence_id,
                    delivery_id=delivery_id,
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
    "/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
    response_model=EvidenceUploadResponse,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def complete_evidence_upload(
    delivery_id: UUID,
    evidence_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> EvidenceUploadResponse:
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        await _authorize_assigned_delivery(
            session,
            delivery_id=delivery_id,
            actor=actor,
        )
        evidence = (
            (
                await session.execute(
                    select(delivery_evidence)
                    .where(
                        delivery_evidence.c.evidence_id == evidence_id,
                        delivery_evidence.c.delivery_id == delivery_id,
                        delivery_evidence.c.captured_by == actor.subject,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if evidence is None:
            raise AppError(404, "delivery_evidence_not_found", "Evidence does not exist.")
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
                    "delivery_evidence_integrity_conflict",
                    "Uploaded evidence type, size, or SHA-256 did not match the intent.",
                )
            await session.execute(
                update(delivery_evidence)
                .where(delivery_evidence.c.evidence_id == evidence_id)
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
    expected_count = (size_bytes + UPLOAD_PART_SIZE - 1) // UPLOAD_PART_SIZE
    by_number = {part.number: part for part in parts}
    if set(by_number) != set(range(1, expected_count + 1)):
        raise AppError(
            409,
            "delivery_evidence_upload_incomplete",
            "Evidence upload parts are incomplete; resume before finalizing.",
        )
    for number, part in by_number.items():
        expected_size = min(UPLOAD_PART_SIZE, size_bytes - (number - 1) * UPLOAD_PART_SIZE)
        if part.size_bytes != expected_size:
            raise AppError(
                409,
                "delivery_evidence_upload_incomplete",
                "Evidence upload part size changed; resume the affected part.",
            )


@router.post(
    "/v1/deliveries/{delivery_id}/evidence/{evidence_id}/access",
    response_model=SignedAccessResponse,
    responses=error_responses(401, 403, 404, 409, 503),
)
async def access_delivery_evidence(
    delivery_id: UUID,
    evidence_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> SignedAccessResponse:
    await _authorize_assigned_delivery(
        session,
        delivery_id=delivery_id,
        actor=actor,
    )
    object_key = await session.scalar(
        select(delivery_evidence.c.object_key).where(
            delivery_evidence.c.evidence_id == evidence_id,
            delivery_evidence.c.delivery_id == delivery_id,
            delivery_evidence.c.status == "verified",
        )
    )
    if object_key is None:
        raise AppError(
            404,
            "delivery_evidence_not_found",
            "Verified Delivery evidence does not exist.",
        )
    return SignedAccessResponse(
        access_url=storage.signed_get_url(object_key=object_key),
        expires_at=datetime.now(UTC) + timedelta(seconds=storage.url_expiry_seconds),
    )


async def _authorized_receipt(
    session: AsyncSession,
    *,
    delivery_receipt_id: UUID,
    actor: AuthorizedUser,
) -> Mapping[str, Any]:
    receipt = (
        (
            await session.execute(
                select(
                    delivery_receipts,
                    delivery_receipt_documents.c.status.label("document_status"),
                    delivery_receipt_documents.c.object_key,
                    delivery_confirmations.c.delivery_id,
                )
                .join(
                    delivery_receipt_documents,
                    delivery_receipts.c.delivery_receipt_id
                    == delivery_receipt_documents.c.delivery_receipt_id,
                )
                .join(
                    delivery_confirmations,
                    delivery_receipts.c.confirmation_id == delivery_confirmations.c.confirmation_id,
                )
                .where(delivery_receipts.c.delivery_receipt_id == delivery_receipt_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if receipt is None:
        raise AppError(404, "delivery_receipt_not_found", "Delivery Receipt does not exist.")
    await _authorize_assigned_delivery(
        session,
        delivery_id=receipt["delivery_id"],
        actor=actor,
    )
    return cast(Mapping[str, Any], receipt)


@router.get(
    "/v1/delivery-receipts/{delivery_receipt_id}",
    response_model=DeliveryReceiptDetailResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_delivery_receipt(
    delivery_receipt_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> DeliveryReceiptDetailResponse:
    receipt = await _authorized_receipt(
        session,
        delivery_receipt_id=delivery_receipt_id,
        actor=actor,
    )
    return DeliveryReceiptDetailResponse(
        delivery_receipt_id=delivery_receipt_id,
        delivery_id=receipt["delivery_id"],
        number=receipt["number"],
        snapshot=dict(receipt["snapshot"]),
        status=receipt["document_status"],
    )


@router.post(
    "/v1/delivery-receipts/{delivery_receipt_id}/access",
    response_model=SignedAccessResponse,
    responses=error_responses(401, 403, 404, 409, 503),
)
async def access_delivery_receipt(
    delivery_receipt_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> SignedAccessResponse:
    receipt = await _authorized_receipt(
        session,
        delivery_receipt_id=delivery_receipt_id,
        actor=actor,
    )
    if receipt["document_status"] != "ready":
        raise AppError(
            409,
            "delivery_receipt_unavailable",
            "The Delivery Receipt document is not available yet.",
        )
    return SignedAccessResponse(
        access_url=storage.signed_get_url(object_key=receipt["object_key"]),
        expires_at=datetime.now(UTC) + timedelta(seconds=storage.url_expiry_seconds),
    )


def _request_hash(command: ConfirmDeliveryCommand, delivery_id: UUID, actor: str) -> str:
    value = f"confirm-delivery:{delivery_id}:{actor}:{command.model_dump_json(exclude_none=False)}"
    return sha256(value.encode()).hexdigest()


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _delivery(
    session: AsyncSession,
    delivery_id: UUID,
) -> Mapping[str, Any] | None:
    row = (
        (
            await session.execute(
                select(
                    delivery_dispatches,
                    delivery_state.c.status.label("delivery_status"),
                    delivery_state.c.assigned_to,
                    delivery_state.c.version.label("delivery_version"),
                    branches.c.code.label("branch_code"),
                    branches.c.company_id,
                    customer_accounts.c.account_number.label("customer_account_number"),
                    customer_accounts.c.legal_name.label("customer_legal_name"),
                )
                .join(
                    delivery_state,
                    delivery_dispatches.c.delivery_id == delivery_state.c.delivery_id,
                )
                .join(branches, delivery_dispatches.c.branch_id == branches.c.branch_id)
                .join(
                    customer_accounts,
                    delivery_dispatches.c.customer_id == customer_accounts.c.customer_id,
                )
                .where(delivery_dispatches.c.delivery_id == delivery_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    return cast(Mapping[str, Any] | None, row)


async def _validated_evidence(
    session: AsyncSession,
    *,
    delivery_id: UUID,
    actor_subject: str,
    evidence_ids: list[UUID],
) -> list[Mapping[str, Any]]:
    if len(set(evidence_ids)) != len(evidence_ids):
        raise AppError(422, "delivery_evidence_invalid", "Evidence identifiers must be unique.")
    rows = list(
        (
            await session.execute(
                select(delivery_evidence)
                .where(delivery_evidence.c.evidence_id.in_(evidence_ids))
                .with_for_update()
            )
        ).mappings()
    )
    if (
        len(rows) != len(evidence_ids)
        or any(
            row["delivery_id"] != delivery_id
            or row["captured_by"] != actor_subject
            or row["status"] != "verified"
            for row in rows
        )
        or not any(row["kind"] == "signature" for row in rows)
    ):
        raise AppError(
            409,
            "delivery_evidence_conflict",
            "Verified signature evidence owned by this assigned Delivery Staff user is required.",
        )
    return [cast(Mapping[str, Any], row) for row in rows]


async def _record_cod_collection(
    session: AsyncSession,
    *,
    command: CODCollectionCommand,
    confirmation_id: UUID,
    delivery: Mapping[str, Any],
    amount_due: Decimal,
    actor: AuthorizedUser,
    correlation_id: str,
    idempotency_key: str,
    base_currency: str,
) -> CODCollectionResponse:
    if "finance:payment-record" not in actor.capabilities:
        raise AppError(
            403,
            "cod_collection_capability_required",
            "Payment recording authority is required to collect Cash on Delivery.",
        )
    if command.currency != base_currency:
        raise AppError(
            409,
            "payment_currency_conflict",
            "COD collection currency must match the Company Base Currency.",
        )
    collected = command.amount.quantize(currency_quantum(base_currency), ROUND_HALF_UP)
    if collected != amount_due:
        raise AppError(
            409,
            "cod_collection_amount_conflict",
            "COD collection must equal the accepted Delivery value exactly.",
            details={"amount_due": str(amount_due), "amount_collected": str(collected)},
        )
    if command.payment_method != "cash":
        receipt = (
            (
                await session.execute(
                    select(
                        payment_receipts,
                        payment_receipt_status.c.state,
                        payment_receipt_balances.c.cleared_amount,
                    )
                    .join(
                        payment_receipt_status,
                        payment_receipts.c.payment_receipt_id
                        == payment_receipt_status.c.payment_receipt_id,
                    )
                    .join(
                        payment_receipt_balances,
                        payment_receipts.c.payment_receipt_id
                        == payment_receipt_balances.c.payment_receipt_id,
                    )
                    .where(payment_receipts.c.payment_receipt_id == command.payment_receipt_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is None:
            raise AppError(
                409,
                "cod_payment_receipt_required",
                "Record the non-cash COD Payment Receipt before confirmation.",
            )
        if (
            receipt["customer_id"] != delivery["customer_id"]
            or receipt["branch_id"] != delivery["branch_id"]
            or receipt["intended_sales_order_id"] != delivery["sales_order_id"]
            or receipt["payment_method_kind"] != command.payment_method
            or receipt["currency"] != base_currency
            or receipt["amount"] != collected
        ):
            raise AppError(
                409,
                "cod_payment_receipt_conflict",
                "The COD Payment Receipt does not match this Delivery collection.",
            )
        if receipt["state"] != "cleared" or receipt["cleared_amount"] < amount_due:
            raise AppError(
                409,
                "cod_payment_verification_required",
                "Non-cash COD collection must clear verification before Delivery Confirmation.",
            )
        await session.execute(
            insert(cod_collections).values(
                confirmation_id=confirmation_id,
                delivery_id=delivery["delivery_id"],
                payment_receipt_id=command.payment_receipt_id,
                amount_due=amount_due,
                amount_collected=collected,
                currency=base_currency,
                status="cleared",
                collected_by=actor.subject,
            )
        )
        return CODCollectionResponse(
            payment_receipt_id=command.payment_receipt_id,
            amount_due=amount_due,
            amount_collected=collected,
            currency=base_currency,
            payment_method=command.payment_method,
            status="cleared",
            application_status="unapplied",
            cash_reconciliation_status=None,
        )
    existing_receipt = await session.scalar(
        select(payment_receipts.c.payment_receipt_id).where(
            payment_receipts.c.payment_receipt_id == command.payment_receipt_id
        )
    )
    if existing_receipt is not None:
        raise AppError(
            409,
            "cod_payment_receipt_conflict",
            "The cash COD Payment Receipt identity is already in use.",
        )
    method = (
        (
            await session.execute(
                select(payment_methods).where(
                    payment_methods.c.company_id == delivery["company_id"],
                    payment_methods.c.kind == "cash",
                    payment_methods.c.is_active.is_(True),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if method is None:
        raise AppError(409, "payment_method_unavailable", "Cash is not configured and active.")
    await session.execute(
        insert(payment_receipts).values(
            payment_receipt_id=command.payment_receipt_id,
            company_id=delivery["company_id"],
            branch_id=delivery["branch_id"],
            customer_id=delivery["customer_id"],
            payment_method_id=method["payment_method_id"],
            payment_method_code=method["code"],
            payment_method_kind=method["kind"],
            amount=collected,
            currency=base_currency,
            received_at=command.received_at,
            external_reference=None,
            external_reference_normalized=None,
            evidence=None,
            intended_sales_order_id=delivery["sales_order_id"],
            intended_fulfillment_order_id=delivery["fulfillment_order_id"],
            recorded_by=actor.subject,
            correlation_id=correlation_id,
            idempotency_key=f"{idempotency_key}:cod-payment",
        )
    )
    for event_type, reason in (
        ("recorded", "COD Payment Receipt recorded with Delivery Confirmation"),
        ("cleared", "Authorized cash COD collection clears immediately"),
    ):
        await session.execute(
            insert(payment_receipt_events).values(
                payment_receipt_event_id=uuid4(),
                payment_receipt_id=command.payment_receipt_id,
                event_type=event_type,
                actor_subject=actor.subject,
                reason=reason,
                evidence=None,
                source_id=confirmation_id,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:cod-payment:{event_type}",
                occurred_at=command.received_at,
            )
        )
    await session.execute(
        insert(payment_receipt_status).values(
            payment_receipt_id=command.payment_receipt_id,
            company_id=delivery["company_id"],
            payment_method_id=method["payment_method_id"],
            state="cleared",
            cleared_at=command.received_at,
        )
    )
    await session.execute(
        insert(payment_receipt_balances).values(
            payment_receipt_id=command.payment_receipt_id,
            cleared_amount=collected,
        )
    )
    await session.execute(
        insert(cash_reconciliation_items).values(
            payment_receipt_id=command.payment_receipt_id,
            status="pending",
            expected_amount=collected,
        )
    )
    await session.execute(
        insert(cod_collections).values(
            confirmation_id=confirmation_id,
            delivery_id=delivery["delivery_id"],
            payment_receipt_id=command.payment_receipt_id,
            amount_due=amount_due,
            amount_collected=collected,
            currency=base_currency,
            status="cleared",
            collected_by=actor.subject,
        )
    )
    return CODCollectionResponse(
        payment_receipt_id=command.payment_receipt_id,
        amount_due=amount_due,
        amount_collected=collected,
        currency=base_currency,
        payment_method="cash",
        status="cleared",
        application_status="unapplied",
        cash_reconciliation_status="pending",
    )


@router.post(
    "/v1/deliveries/{delivery_id}/cod-on-account-conversions",
    response_model=CODOnAccountConversionResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def convert_cod_on_account(
    delivery_id: UUID,
    command: ConvertCODOnAccountCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_cod_on_account_converter)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CODOnAccountConversionResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = sha256(
        (
            f"convert-cod-on-account:{delivery_id}:{actor.subject}:"
            f"{command.model_dump_json(exclude_none=False)}"
        ).encode()
    ).hexdigest()
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return CODOnAccountConversionResponse.model_validate(replay)
        delivery = await _delivery(session, delivery_id)
        if delivery is None:
            raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
        if delivery["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if actor.subject == delivery["assigned_to"]:
            raise AppError(
                409,
                "maker_checker_violation",
                "Assigned Delivery Staff cannot approve their own unpaid COD conversion.",
            )
        if (
            delivery["payment_timing_policy"] != "cash_on_delivery"
            or delivery["delivery_status"] != "dispatched"
            or delivery["delivery_version"] != command.expected_delivery_version
        ):
            raise AppError(
                409,
                "delivery_version_conflict",
                "The Cash on Delivery shipment changed; refresh before conversion.",
            )
        existing_conversion = await session.scalar(
            select(cod_on_account_conversions.c.conversion_id).where(
                cod_on_account_conversions.c.delivery_id == delivery_id
            )
        )
        if existing_conversion is not None:
            raise AppError(
                409,
                "cod_on_account_conversion_exists",
                "This Delivery already has an On Account conversion decision.",
            )
        lines = list(
            (
                await session.execute(
                    select(
                        delivery_lines.c.line_id,
                        delivery_lines.c.quantity_base,
                        sales_order_line_revisions.c.quantity_base.label("source_quantity_base"),
                        sales_order_line_revisions.c.allocated_discount.label(
                            "source_allocated_discount"
                        ),
                        sales_order_line_revisions.c.tax_amount.label("source_tax_amount"),
                        sales_order_line_revisions.c.line_total.label("source_line_total"),
                        sales_order_line_revisions.c.calculation_snapshot.label(
                            "source_calculation_snapshot"
                        ),
                    )
                    .join(
                        sales_order_line_revisions,
                        (
                            sales_order_line_revisions.c.sales_order_revision_id
                            == delivery["sales_order_revision_id"]
                        )
                        & (sales_order_line_revisions.c.line_id == delivery_lines.c.line_id),
                    )
                    .where(delivery_lines.c.delivery_id == delivery_id)
                    .with_for_update()
                )
            ).mappings()
        )
        accepted: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
        for line in lines:
            accepted[line["line_id"]] += line["quantity_base"]
        base_currency = cast(str, await session.scalar(select(companies.c.base_currency).limit(1)))
        amount = await calculate_cod_amount_due(
            session,
            sales_order_revision_id=delivery["sales_order_revision_id"],
            lines=cast(Sequence[Mapping[str, Any]], lines),
            accepted=accepted,
            currency=base_currency,
        )
        authority = (
            (
                await session.execute(
                    select(approval_authorities).where(
                        approval_authorities.c.user_subject == actor.subject,
                        approval_authorities.c.capability_code == "sales:credit-override",
                        approval_authorities.c.branch_id == delivery["branch_id"],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        await _lock(session, f"credit:{delivery['customer_id']}")
        exposure = (
            (
                await session.execute(
                    select(customer_credit_exposure)
                    .where(customer_credit_exposure.c.customer_id == delivery["customer_id"])
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        customer = (
            (
                await session.execute(
                    select(customer_accounts)
                    .where(customer_accounts.c.customer_id == delivery["customer_id"])
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if customer["status"] != "active":
            raise AppError(409, "customer_inactive", "The Customer Account is not active.")
        if customer["credit_hold"]:
            raise AppError(409, "customer_credit_hold", "The Customer Account is on Credit Hold.")
        if not customer["payment_terms"].strip():
            raise AppError(
                409,
                "payment_terms_required",
                "On Account conversion requires Customer payment terms.",
            )
        open_balance = exposure["open_balance"] if exposure is not None else ZERO
        approved_before = exposure["approved_uninvoiced"] if exposure is not None else ZERO
        projected = open_balance + approved_before + amount
        credit_limit = customer["credit_limit"]
        excess = max(projected - credit_limit, ZERO) if credit_limit is not None else projected
        if excess > ZERO and (
            authority is None
            or (authority["maximum_amount"] is not None and excess > authority["maximum_amount"])
        ):
            raise AppError(
                403,
                "approval_authority_required",
                "Credit Override Approval Authority must cover the projected credit excess.",
            )
        commercial_approval_id = await session.scalar(
            select(commercial_approvals.c.commercial_approval_id).where(
                commercial_approvals.c.sales_order_revision_id
                == delivery["sales_order_revision_id"]
            )
        )
        if commercial_approval_id is None:
            raise AppError(409, "commercial_approval_required", "Approval is unavailable.")
        await session.execute(
            insert(cod_on_account_conversions).values(
                conversion_id=command.conversion_id,
                delivery_id=delivery_id,
                commercial_approval_id=commercial_approval_id,
                amount=amount,
                currency=base_currency,
                open_balance_snapshot=open_balance,
                approved_uninvoiced_snapshot=approved_before,
                credit_limit_snapshot=credit_limit,
                credit_excess_approved=excess,
                reason=command.reason,
                approved_by=actor.subject,
                status="approved",
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        if exposure is None:
            await session.execute(
                insert(customer_credit_exposure).values(
                    customer_id=delivery["customer_id"],
                    open_balance=ZERO,
                    approved_uninvoiced=amount,
                )
            )
        else:
            await session.execute(
                update(customer_credit_exposure)
                .where(customer_credit_exposure.c.customer_id == delivery["customer_id"])
                .values(
                    approved_uninvoiced=customer_credit_exposure.c.approved_uninvoiced + amount,
                    version=customer_credit_exposure.c.version + 1,
                    updated_at=func.now(),
                )
            )
        await session.execute(
            insert(credit_exposure_entries).values(
                entry_id=uuid4(),
                customer_id=delivery["customer_id"],
                commercial_approval_id=commercial_approval_id,
                sales_order_id=delivery["sales_order_id"],
                component="approved_uninvoiced",
                amount_delta=amount,
                source_type="cod_on_account_conversion",
                source_id=command.conversion_id,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=f"{idempotency_key}:credit",
            )
        )
        result = CODOnAccountConversionResponse(
            conversion_id=command.conversion_id,
            delivery_id=delivery_id,
            amount=amount,
            currency=base_currency,
            status="approved",
            approved_by=actor.subject,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result


async def _decrement_transit(
    session: AsyncSession,
    *,
    delivery_line: Mapping[str, Any],
    transit_location_id: UUID,
) -> None:
    assignments = list(
        (
            await session.execute(
                select(pick_identity_assignments).where(
                    pick_identity_assignments.c.pick_line_id == delivery_line["pick_line_id"]
                )
            )
        ).mappings()
    )
    tracking_policy = delivery_line["tracking_policy"]
    assigned_policies = {assignment["tracking_policy"] for assignment in assignments}
    assigned_quantity = sum(
        (cast(Decimal, assignment["quantity_base"]) for assignment in assignments),
        ZERO,
    )
    valid_tracking = (
        (tracking_policy == "untracked" and not assignments)
        or (
            tracking_policy == "lot"
            and assigned_policies == {"lot"}
            and assigned_quantity == delivery_line["quantity_base"]
        )
        or (
            tracking_policy == "serial"
            and assigned_policies == {"serial"}
            and assigned_quantity == delivery_line["quantity_base"]
        )
    )
    if not valid_tracking:
        raise AppError(
            409,
            "delivery_tracking_policy_conflict",
            "Current SKU Tracking Policy no longer matches the dispatched identities.",
        )
    positions: list[tuple[str, Decimal]]
    if not assignments:
        positions = [("", cast(Decimal, delivery_line["quantity_base"]))]
    else:
        positions = []
        for assignment in assignments:
            if assignment["tracking_policy"] == "lot":
                lot_code = await session.scalar(
                    text("SELECT lot_code FROM lot_identities WHERE lot_identity_id = :id"),
                    {"id": assignment["lot_identity_id"]},
                )
                positions.append((f"lot:{lot_code}", assignment["quantity_base"]))
            else:
                serial_number = await session.scalar(
                    text(
                        "SELECT serial_number FROM stock_serial_allocations "
                        "WHERE serial_allocation_id = :id"
                    ),
                    {"id": assignment["serial_allocation_id"]},
                )
                positions.append((f"serial:{serial_number}", Decimal("1")))
    for identity_key, quantity in positions:
        current = (
            (
                await session.execute(
                    select(inventory_availability)
                    .where(
                        inventory_availability.c.sku_id == delivery_line["sku_id"],
                        inventory_availability.c.warehouse_id == delivery_line["warehouse_id"],
                        inventory_availability.c.location_id == transit_location_id,
                        inventory_availability.c.identity_key == identity_key,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if current is None or current["on_hand"] < quantity:
            raise AppError(
                409,
                "delivery_inventory_conflict",
                "In Transit quantity or tracked identity changed; refresh before confirming.",
            )
        await session.execute(
            update(inventory_availability)
            .where(
                inventory_availability.c.sku_id == delivery_line["sku_id"],
                inventory_availability.c.warehouse_id == delivery_line["warehouse_id"],
                inventory_availability.c.location_id == transit_location_id,
                inventory_availability.c.identity_key == identity_key,
            )
            .values(
                on_hand=inventory_availability.c.on_hand - quantity,
                serial_numbers=[]
                if identity_key.startswith("serial:")
                else current["serial_numbers"],
            )
        )


def _receipt_line_snapshot(
    lines: Sequence[Any],
    line_id: UUID,
    sku_id: UUID,
    accepted_quantity: Decimal,
) -> dict[str, object]:
    source = next(line for line in lines if line["line_id"] == line_id and line["sku_id"] == sku_id)
    conversion_snapshot = dict(source["source_conversion_snapshot"])
    accepted_quantity_entered = (
        accepted_quantity / Decimal(conversion_snapshot["base_quantity_per_unit"])
    ).quantize(SIX_PLACES)
    return {
        "line_id": str(line_id),
        "sales_order_line_revision_id": str(source["sales_order_line_revision_id"]),
        "sku_id": str(sku_id),
        "sku_code": source["sku_code"],
        "sku_name": source["sku_name"],
        "entered_unit": source["source_entered_unit"],
        "conversion_snapshot": conversion_snapshot,
        "accepted_quantity_entered": str(accepted_quantity_entered),
        "accepted_quantity_base": str(accepted_quantity),
        "approved_unit_price": str(source["effective_unit_price"]),
        "calculation_snapshot": dict(source["source_calculation_snapshot"]),
    }


@router.post(
    "/v1/deliveries/{delivery_id}/confirmation-quote",
    response_model=ConfirmationQuoteResponse,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def quote_delivery_confirmation(
    delivery_id: UUID,
    command: ConfirmationQuoteCommand,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ConfirmationQuoteResponse:
    delivery = await _delivery(session, delivery_id)
    if delivery is None:
        raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
    if delivery["branch_id"] not in actor.branch_ids or delivery["assigned_to"] != actor.subject:
        raise AppError(403, "delivery_assignment_required", "Assigned Delivery access is required.")
    if delivery["delivery_status"] != "dispatched":
        raise AppError(409, "delivery_already_confirmed", "The Delivery is already confirmed.")
    if delivery["delivery_version"] != command.expected_delivery_version:
        raise AppError(409, "delivery_version_conflict", "The Delivery changed; refresh.")
    lines = list(
        (
            await session.execute(
                select(
                    delivery_lines,
                    sales_order_line_revisions.c.quantity_base.label("source_quantity_base"),
                    sales_order_line_revisions.c.allocated_discount.label(
                        "source_allocated_discount"
                    ),
                    sales_order_line_revisions.c.tax_amount.label("source_tax_amount"),
                    sales_order_line_revisions.c.line_total.label("source_line_total"),
                    sales_order_line_revisions.c.calculation_snapshot.label(
                        "source_calculation_snapshot"
                    ),
                    skus.c.tracking_policy,
                )
                .join(
                    sales_order_line_revisions,
                    (
                        sales_order_line_revisions.c.sales_order_revision_id
                        == delivery["sales_order_revision_id"]
                    )
                    & (sales_order_line_revisions.c.line_id == delivery_lines.c.line_id),
                )
                .join(skus, delivery_lines.c.sku_id == skus.c.sku_id)
                .where(delivery_lines.c.delivery_id == delivery_id)
                .order_by(delivery_lines.c.line_id, delivery_lines.c.delivery_line_id)
            )
        ).mappings()
    )
    partitions = validate_delivery_partitions(
        cast(Sequence[Mapping[str, Any]], lines), command.lines
    )
    accepted: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    accepted_total = ZERO
    for line in lines:
        line_command = partitions[line["delivery_line_id"]]
        await validate_delivery_identity_partitions(
            session, cast(Mapping[str, Any], line), line_command
        )
        quantity = cast(Decimal, line_command.accepted_quantity_base)
        accepted[line["line_id"]] += quantity
        accepted_total += quantity
    currency = cast(str, await session.scalar(select(companies.c.base_currency).limit(1)))
    amount_due = (
        await calculate_cod_amount_due(
            session,
            sales_order_revision_id=delivery["sales_order_revision_id"],
            lines=cast(Sequence[Mapping[str, Any]], lines),
            accepted=accepted,
            currency=currency,
        )
        if delivery["payment_timing_policy"] == "cash_on_delivery"
        else ZERO
    )
    return ConfirmationQuoteResponse(
        delivery_id=delivery_id,
        delivery_version=delivery["delivery_version"],
        accepted_quantity_base=accepted_total,
        amount_due=amount_due,
        currency=currency,
    )


@router.post(
    "/v1/deliveries/{delivery_id}/confirmations",
    response_model=DeliveryConfirmationResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def confirm_delivery(
    delivery_id: UUID,
    command: ConfirmDeliveryCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> DeliveryConfirmationResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash(command, delivery_id, actor.subject)
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        delivery = await _delivery(session, delivery_id)
        if delivery is None:
            raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
        if delivery["assigned_to"] != actor.subject:
            raise AppError(
                403,
                "delivery_assignment_required",
                "The Delivery is no longer assigned to this user.",
            )
        if delivery["branch_id"] not in actor.branch_ids:
            raise AppError(
                403, "operational_scope_required", "Branch Operational Scope is required."
            )
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return DeliveryConfirmationResponse.model_validate(replay)
        settlement_count = int(command.collection is not None) + int(
            command.on_account_conversion_id is not None
        )
        if delivery["payment_timing_policy"] == "cash_on_delivery" and settlement_count > 1:
            raise AppError(
                409,
                "cod_collection_required",
                "Cash on Delivery accepts at most one collection or On Account conversion.",
            )
        if delivery["payment_timing_policy"] != "cash_on_delivery" and settlement_count != 0:
            raise AppError(
                409,
                "cod_collection_not_applicable",
                "Collection is only accepted for Cash on Delivery.",
            )
        if (
            delivery["delivery_status"] != "dispatched"
            or delivery["delivery_version"] != command.expected_delivery_version
        ):
            raise AppError(
                409, "delivery_version_conflict", "The Delivery changed; refresh before retrying."
            )
        exception_evidence_ids = {
            evidence_id
            for line in command.lines
            for outcome in ("refused", "damaged", "short_missing", "still_undelivered")
            if (detail := getattr(line.exception_details, outcome)) is not None
            for evidence_id in detail.evidence_ids
        }
        all_evidence_ids = list(dict.fromkeys([*command.evidence_ids, *exception_evidence_ids]))
        await _validated_evidence(
            session,
            delivery_id=delivery_id,
            actor_subject=actor.subject,
            evidence_ids=all_evidence_ids,
        )
        photo_evidence_ids = set(
            (
                await session.execute(
                    select(delivery_evidence.c.evidence_id).where(
                        delivery_evidence.c.delivery_id == delivery_id,
                        delivery_evidence.c.evidence_id.in_(exception_evidence_ids),
                        delivery_evidence.c.kind == "photo",
                        delivery_evidence.c.status == "verified",
                    )
                )
            ).scalars()
        )
        for line_command in command.lines:
            for outcome in ("damaged", "short_missing"):
                detail = getattr(line_command.exception_details, outcome)
                if detail is not None and not photo_evidence_ids.intersection(detail.evidence_ids):
                    raise AppError(
                        409,
                        "delivery_exception_photo_required",
                        "Each damaged and short or missing outcome requires "
                        "verified photo evidence.",
                    )
        lines = list(
            (
                await session.execute(
                    select(
                        delivery_lines,
                        pick_lines.c.entered_unit.label("movement_entered_unit"),
                        sales_order_line_revisions.c.sales_order_line_revision_id,
                        sales_order_line_revisions.c.sku_code,
                        sales_order_line_revisions.c.sku_name,
                        sales_order_line_revisions.c.entered_unit.label("source_entered_unit"),
                        sales_order_line_revisions.c.conversion_snapshot.label(
                            "source_conversion_snapshot"
                        ),
                        sales_order_line_revisions.c.effective_unit_price,
                        sales_order_line_revisions.c.quantity_base.label("source_quantity_base"),
                        sales_order_line_revisions.c.allocated_discount.label(
                            "source_allocated_discount"
                        ),
                        sales_order_line_revisions.c.tax_amount.label("source_tax_amount"),
                        sales_order_line_revisions.c.line_total.label("source_line_total"),
                        sales_order_line_revisions.c.calculation_snapshot.label(
                            "source_calculation_snapshot"
                        ),
                        skus.c.tracking_policy,
                    )
                    .join(pick_lines, delivery_lines.c.pick_line_id == pick_lines.c.pick_line_id)
                    .join(
                        sales_order_line_revisions,
                        (
                            sales_order_line_revisions.c.sales_order_revision_id
                            == delivery["sales_order_revision_id"]
                        )
                        & (sales_order_line_revisions.c.line_id == delivery_lines.c.line_id),
                    )
                    .join(skus, delivery_lines.c.sku_id == skus.c.sku_id)
                    .where(delivery_lines.c.delivery_id == delivery_id)
                    .order_by(delivery_lines.c.line_id, delivery_lines.c.delivery_line_id)
                    .with_for_update()
                )
            ).mappings()
        )
        partition_lines = cast(Sequence[Mapping[str, Any]], lines)
        partitions = validate_delivery_partitions(partition_lines, command.lines)
        accepted_by_order_line: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
        accepted_total = ZERO
        for line in lines:
            accepted = partitions[line["delivery_line_id"]].accepted_quantity_base
            accepted_by_order_line[line["line_id"]] += accepted
            accepted_total += accepted
        if delivery["payment_timing_policy"] == "cash_on_delivery" and (
            (accepted_total > ZERO and settlement_count != 1)
            or (accepted_total == ZERO and settlement_count != 0)
        ):
            raise AppError(
                409,
                "cod_collection_required",
                "COD settlement must match whether accepted quantity has value due.",
            )
        await session.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtext('inventory-projection-rebuild'))")
        )
        for sku_id in sorted({line["sku_id"] for line in lines}, key=str):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:stock_key))"),
                {"stock_key": f"{sku_id}:{delivery['warehouse_id']}"},
            )
        transit_location_id = await session.scalar(
            select(warehouse_stock_locations.c.location_id).where(
                warehouse_stock_locations.c.warehouse_id == delivery["warehouse_id"],
                warehouse_stock_locations.c.custody == "in_transit",
            )
        )
        if transit_location_id is None:
            raise AppError(
                409, "delivery_inventory_conflict", "The In Transit location is unavailable."
            )
        investigation_location_id = await ensure_custody_location(
            session,
            warehouse_id=delivery["warehouse_id"],
            custody="investigation",
            actor_subject=actor.subject,
        )
        base_currency = cast(str, await session.scalar(select(companies.c.base_currency).limit(1)))
        next_version = delivery["delivery_version"] + 1
        await session.execute(
            insert(delivery_confirmations).values(
                confirmation_id=command.confirmation_id,
                delivery_id=delivery_id,
                recipient_name=command.recipient_name,
                device_captured_at=command.device_captured_at,
                notes=command.notes,
                confirmed_by=actor.subject,
                delivery_version=next_version,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        if delivery["payment_timing_policy"] == "cash_on_delivery" and accepted_total == ZERO:
            unused_conversion = (
                (
                    await session.execute(
                        select(cod_on_account_conversions)
                        .where(
                            cod_on_account_conversions.c.delivery_id == delivery_id,
                            cod_on_account_conversions.c.status == "approved",
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if unused_conversion is not None:
                await _lock(session, f"credit:{delivery['customer_id']}")
                exposure = (
                    (
                        await session.execute(
                            select(customer_credit_exposure)
                            .where(
                                customer_credit_exposure.c.customer_id == delivery["customer_id"]
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                released = cast(Decimal, unused_conversion["amount"])
                if exposure["approved_uninvoiced"] < released:
                    raise AppError(
                        409,
                        "credit_exposure_conflict",
                        "Approved credit exposure changed; refresh before confirmation.",
                    )
                await session.execute(
                    update(customer_credit_exposure)
                    .where(customer_credit_exposure.c.customer_id == delivery["customer_id"])
                    .values(
                        approved_uninvoiced=(
                            customer_credit_exposure.c.approved_uninvoiced - released
                        ),
                        version=customer_credit_exposure.c.version + 1,
                        updated_at=func.now(),
                    )
                )
                await session.execute(
                    insert(credit_exposure_entries).values(
                        entry_id=uuid4(),
                        customer_id=delivery["customer_id"],
                        commercial_approval_id=unused_conversion["commercial_approval_id"],
                        sales_order_id=delivery["sales_order_id"],
                        component="approved_uninvoiced",
                        amount_delta=-released,
                        source_type="cod_on_account_conversion_release",
                        source_id=unused_conversion["conversion_id"],
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=f"{idempotency_key}:zero-accepted-credit-release",
                    )
                )
                await session.execute(
                    update(cod_on_account_conversions)
                    .where(
                        cod_on_account_conversions.c.conversion_id
                        == unused_conversion["conversion_id"]
                    )
                    .values(status="reversed", consumed_amount=ZERO)
                )
        collection_response: CODCollectionResponse | None = None
        conversion_response: CODOnAccountConversionResponse | None = None
        amount_due = (
            await calculate_cod_amount_due(
                session,
                sales_order_revision_id=delivery["sales_order_revision_id"],
                lines=cast(Sequence[Mapping[str, Any]], lines),
                accepted=accepted_by_order_line,
                currency=base_currency,
                current_confirmation_id=command.confirmation_id,
            )
            if settlement_count == 1
            else ZERO
        )
        if command.collection is not None:
            collection_response = await _record_cod_collection(
                session,
                command=command.collection,
                confirmation_id=command.confirmation_id,
                delivery=delivery,
                amount_due=amount_due,
                actor=actor,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                base_currency=base_currency,
            )
        if command.on_account_conversion_id is not None:
            conversion = (
                (
                    await session.execute(
                        select(cod_on_account_conversions)
                        .where(
                            cod_on_account_conversions.c.conversion_id
                            == command.on_account_conversion_id
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                conversion is None
                or conversion["delivery_id"] != delivery_id
                or conversion["status"] != "approved"
                or conversion["amount"] < amount_due
                or conversion["currency"] != base_currency
                or conversion["approved_by"] == actor.subject
            ):
                raise AppError(
                    409,
                    "cod_on_account_conversion_conflict",
                    "A distinct approval covering the unpaid accepted COD value is required.",
                )
            unused_amount = cast(Decimal, conversion["amount"]) - amount_due
            if unused_amount > ZERO:
                await _lock(session, f"credit:{delivery['customer_id']}")
                exposure = (
                    (
                        await session.execute(
                            select(customer_credit_exposure)
                            .where(
                                customer_credit_exposure.c.customer_id == delivery["customer_id"]
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                if exposure["approved_uninvoiced"] < unused_amount:
                    raise AppError(
                        409,
                        "credit_exposure_conflict",
                        "Approved credit exposure changed; refresh before confirmation.",
                    )
                await session.execute(
                    update(customer_credit_exposure)
                    .where(customer_credit_exposure.c.customer_id == delivery["customer_id"])
                    .values(
                        approved_uninvoiced=(
                            customer_credit_exposure.c.approved_uninvoiced - unused_amount
                        ),
                        version=customer_credit_exposure.c.version + 1,
                        updated_at=func.now(),
                    )
                )
                await session.execute(
                    insert(credit_exposure_entries).values(
                        entry_id=uuid4(),
                        customer_id=delivery["customer_id"],
                        commercial_approval_id=conversion["commercial_approval_id"],
                        sales_order_id=delivery["sales_order_id"],
                        component="approved_uninvoiced",
                        amount_delta=-unused_amount,
                        source_type="cod_on_account_conversion_release",
                        source_id=command.on_account_conversion_id,
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=f"{idempotency_key}:credit-release",
                    )
                )
            await session.execute(
                update(cod_on_account_conversions)
                .where(
                    cod_on_account_conversions.c.conversion_id == command.on_account_conversion_id
                )
                .values(
                    status="consumed",
                    confirmation_id=command.confirmation_id,
                    consumed_amount=amount_due,
                )
            )
            conversion_response = CODOnAccountConversionResponse(
                conversion_id=command.on_account_conversion_id,
                delivery_id=delivery_id,
                amount=amount_due,
                currency=base_currency,
                status="consumed",
                approved_by=conversion["approved_by"],
            )
        response_line_items: list[ConfirmationLineResponse] = []
        receipt_response_lines: dict[tuple[UUID, UUID], dict[str, Any]] = {}
        sku_totals: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
        for line in lines:
            valuation = (
                (
                    await session.execute(
                        select(inventory_valuation)
                        .where(
                            inventory_valuation.c.sku_id == line["sku_id"],
                            inventory_valuation.c.warehouse_id == delivery["warehouse_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            unit_cost = cast(Decimal, valuation["moving_average_unit_cost"]).quantize(SIX_PLACES)
            line_command = partitions[line["delivery_line_id"]]
            posted = await post_delivery_partition_line(
                session,
                confirmation_id=command.confirmation_id,
                line=cast(Mapping[str, Any], line),
                command=line_command,
                warehouse_id=delivery["warehouse_id"],
                transit_location_id=cast(UUID, transit_location_id),
                investigation_location_id=investigation_location_id,
                unit_cost=unit_cost,
                base_currency=base_currency,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
            accepted_quantity = cast(Decimal, posted["accepted_quantity_base"])
            sku_totals[line["sku_id"]] += accepted_quantity
            response_line_items.append(
                ConfirmationLineResponse(
                    delivery_line_id=line["delivery_line_id"],
                    line_id=line["line_id"],
                    sku_id=line["sku_id"],
                    accepted_quantity_base=accepted_quantity,
                    refused_quantity_base=line_command.refused_quantity_base,
                    damaged_quantity_base=line_command.damaged_quantity_base,
                    short_missing_quantity_base=line_command.short_missing_quantity_base,
                    still_undelivered_quantity_base=(line_command.still_undelivered_quantity_base),
                    unit_cost=unit_cost,
                    value_delta=posted["value_delta"],
                    outbound_movement_id=posted["outbound_movement_id"],
                )
            )
            key = (line["line_id"], line["sku_id"])
            aggregate = receipt_response_lines.setdefault(
                key,
                {
                    "quantity": ZERO,
                    "value": ZERO,
                    "unit_cost": unit_cost,
                    "movement_id": posted["outbound_movement_id"],
                },
            )
            aggregate["quantity"] += accepted_quantity
            aggregate["value"] += posted["value_delta"]
        for sku_id, quantity in sku_totals.items():
            await session.execute(
                update(inventory_valuation)
                .where(
                    inventory_valuation.c.sku_id == sku_id,
                    inventory_valuation.c.warehouse_id == delivery["warehouse_id"],
                )
                .values(
                    quantity_on_hand=inventory_valuation.c.quantity_on_hand - quantity,
                    inventory_value=inventory_valuation.c.inventory_value
                    - quantity * inventory_valuation.c.moving_average_unit_cost,
                )
            )
        await session.execute(
            insert(delivery_confirmation_evidence),
            [
                {"confirmation_id": command.confirmation_id, "evidence_id": evidence_id}
                for evidence_id in command.evidence_ids
            ],
        )
        receipt_response: DeliveryReceiptResponse | None = None
        receipt_id: UUID | None = None
        if accepted_total > ZERO:
            series = (
                (
                    await session.execute(
                        select(document_series)
                        .where(
                            document_series.c.branch_id == delivery["branch_id"],
                            document_series.c.document_type == "delivery_receipt",
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if series is None:
                raise AppError(
                    409,
                    "delivery_receipt_series_required",
                    "A Branch Delivery Receipt Document Series must be configured "
                    "before confirmation.",
                )
            series_id = series["document_series_id"]
            series_number = series["next_number"]
            prefix = series["prefix"]
            await session.execute(
                update(document_series)
                .where(document_series.c.document_series_id == series_id)
                .values(next_number=document_series.c.next_number + 1)
            )
            receipt_id = uuid4()
            receipt_number = f"{prefix}-{series_number:08d}"
            receipt_snapshot = {
                "delivery_id": str(delivery_id),
                "confirmation_id": str(command.confirmation_id),
                "sales_order_id": str(delivery["sales_order_id"]),
                "customer_id": str(delivery["customer_id"]),
                "customer_account_number": delivery["customer_account_number"],
                "customer_legal_name": delivery["customer_legal_name"],
                "recipient_name": command.recipient_name,
                "delivery_address": dict(delivery["delivery_address_snapshot"]),
                "evidence_ids": [str(value) for value in command.evidence_ids],
                "lines": [
                    _receipt_line_snapshot(lines, line_id, sku_id, data["quantity"])
                    for (line_id, sku_id), data in sorted(
                        receipt_response_lines.items(), key=lambda item: str(item[0][0])
                    )
                    if data["quantity"] > ZERO
                ],
            }
            await session.execute(
                insert(delivery_receipts).values(
                    delivery_receipt_id=receipt_id,
                    confirmation_id=command.confirmation_id,
                    document_series_id=series_id,
                    branch_id=delivery["branch_id"],
                    series_number=series_number,
                    number=receipt_number,
                    snapshot=receipt_snapshot,
                )
            )
            await session.execute(
                insert(delivery_receipt_documents).values(
                    delivery_receipt_id=receipt_id,
                    status="pending_document",
                    object_key=f"delivery-receipts/{receipt_id}.pdf",
                )
            )
            await session.execute(
                insert(document_series_number_audit).values(
                    document_series_number_audit_id=uuid4(),
                    document_series_id=series_id,
                    series_number=series_number,
                    status="issued",
                    delivery_receipt_id=receipt_id,
                )
            )
            receipt_response = DeliveryReceiptResponse(
                delivery_receipt_id=receipt_id,
                number=receipt_number,
                status="pending_document",
            )
        event_id: UUID | None = None
        if accepted_total > ZERO:
            event_id = uuid4()
            await session.execute(
                insert(outbox_events).values(
                    outbox_event_id=event_id,
                    aggregate_type="delivery",
                    aggregate_id=delivery_id,
                    event_type="delivery.confirmed.v1",
                    payload={
                        "delivery_id": str(delivery_id),
                        "confirmation_id": str(command.confirmation_id),
                        "delivery_receipt_id": str(receipt_id),
                    },
                    correlation_id=request.state.correlation_id,
                )
            )
            await session.execute(insert(outbox_processing_state).values(outbox_event_id=event_id))
        await session.execute(
            update(delivery_state)
            .where(delivery_state.c.delivery_id == delivery_id)
            .values(status="confirmed", version=next_version, updated_at=func.now())
        )
        await session.execute(
            update(fulfillment_order_state)
            .where(
                fulfillment_order_state.c.fulfillment_order_id == delivery["fulfillment_order_id"]
            )
            .values(
                status=case(
                    (
                        fulfillment_order_state.c.delivered_quantity_base + accepted_total
                        >= fulfillment_order_state.c.reserved_quantity_base,
                        "delivered",
                    ),
                    else_="partially_delivered",
                ),
                delivered_quantity_base=(
                    fulfillment_order_state.c.delivered_quantity_base + accepted_total
                ),
                version=fulfillment_order_state.c.version + 1,
                updated_at=func.now(),
            )
        )
        result = DeliveryConfirmationResponse(
            confirmation_id=command.confirmation_id,
            delivery_id=delivery_id,
            status="confirmed",
            version=next_version,
            lines=response_line_items,
            delivery_receipt=receipt_response,
            outbox_event_id=event_id,
            collection=collection_response,
            on_account_conversion=conversion_response,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result
