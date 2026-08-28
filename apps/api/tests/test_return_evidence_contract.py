from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_delivery_confirmation_contract import FakeObjectStorage
from test_delivery_confirmation_contract import confirmation_client as confirmation_client
from test_delivery_confirmation_contract import confirmation_settings as confirmation_settings
from test_delivery_confirmation_contract import fake_storage as fake_storage
from test_delivery_correction_contract import _confirm_fully_accepted_delivery
from test_payment_clearance_contract import auth
from test_return_authorization_contract import _grant_return_capabilities
from tradeflow_api.config import Settings
from tradeflow_api.object_storage import UploadedPart


async def _grant_return_evidence_capabilities(postgres_url: str) -> None:
    await _grant_return_capabilities(postgres_url)
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO capabilities(code)
                VALUES ('returns:evidence-capture'), ('returns:evidence-read')
                ON CONFLICT (code) DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO role_template_capabilities(role_template_id, capability_code)
                SELECT role_template_id, capability.code
                FROM role_templates
                CROSS JOIN (
                  VALUES ('returns:evidence-capture'), ('returns:evidence-read')
                ) capability(code)
                WHERE role_templates.code = 'WAREHOUSE_SUPERVISOR'
                ON CONFLICT DO NOTHING
                """
            )
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_capturer_adds_note_evidence_to_return_request(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    await _grant_return_evidence_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())

    await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "evidence-return-request"},
        ),
        json={
            "return_request_id": request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "quantity_base": "1.000000",
                }
            ],
        },
    )

    evidence_id = str(uuid4())
    note = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/evidence/notes",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        json={
            "evidence_id": evidence_id,
            "device_captured_at": "2026-08-28T12:00:00Z",
            "note_text": "Customer sealed-unit defect photo pending.",
        },
    )
    assert note.status_code == 201, note.text
    payload = note.json()
    assert payload["evidence_id"] == evidence_id
    assert payload["kind"] == "note"
    assert payload["status"] == "verified"
    assert payload["note_text"] == "Customer sealed-unit defect photo pending."

    listed = await confirmation_client.get(
        f"/v1/return-requests/{request_id}/evidence",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["evidence_id"] == evidence_id


@pytest.mark.asyncio
async def test_capturer_uploads_and_completes_photo_evidence(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    await _grant_return_evidence_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())

    await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "photo-evidence-return-request"},
        ),
        json={
            "return_request_id": request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "quantity_base": "1.000000",
                }
            ],
        },
    )

    evidence_id = str(uuid4())
    fake_storage.multipart_parts = []
    intent = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/evidence/uploads",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "photo",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-28T12:00:00Z",
        },
    )
    assert intent.status_code == 201, intent.text
    assert intent.json()["status"] == "uploading"
    assert len(intent.json()["parts"]) == 1

    fake_storage.head_content_type = "image/png"
    fake_storage.head_size_bytes = 12
    fake_storage.head_sha256 = "a" * 64
    fake_storage.computed_digest = "a" * 64
    fake_storage.multipart_parts = [UploadedPart(etag='"part-etag"', number=1, size_bytes=12)]
    object_key = f"return-requests/{request_id}/evidence/{evidence_id}"
    fake_storage.completed_objects.add(object_key)

    completed = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/evidence/{evidence_id}/complete",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "verified"

    access = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/evidence/{evidence_id}/access",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert access.status_code == 200, access.text
    assert access.json()["access_url"] == f"https://objects.test/{object_key}"


@pytest.mark.asyncio
async def test_offline_evidence_sync_is_acknowledged_when_version_matches(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    await _grant_return_evidence_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())

    await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "offline-sync-return-request"},
        ),
        json={
            "return_request_id": request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "quantity_base": "1.000000",
                }
            ],
        },
    )

    sync = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/offline-evidence",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        json={
            "expected_request_version": 1,
            "correlation_id": "offline-sync-1",
            "evidence": [
                {
                    "evidence_id": str(uuid4()),
                    "kind": "note",
                    "note_text": "Captured offline before authorization.",
                }
            ],
        },
    )
    assert sync.status_code == 200, sync.text
    payload = sync.json()
    assert payload["status"] == "acknowledged"
    assert payload["expected_version"] == 1
    assert payload["current_version"] == 1


@pytest.mark.asyncio
async def test_offline_evidence_sync_detects_conflict_when_request_is_authorized(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    await _grant_return_evidence_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())

    await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "conflict-return-request"},
        ),
        json={
            "return_request_id": request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "quantity_base": "1.000000",
                }
            ],
        },
    )

    authz = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "conflict-authorize"},
        ),
        json={"expected_request_version": 1},
    )
    assert authz.status_code == 200, authz.text

    sync = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/offline-evidence",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        json={
            "expected_request_version": 1,
            "correlation_id": "offline-sync-conflict",
            "evidence": [
                {
                    "evidence_id": str(uuid4()),
                    "kind": "note",
                    "note_text": "Captured offline before authorization.",
                }
            ],
        },
    )
    assert sync.status_code == 200, sync.text
    payload = sync.json()
    assert payload["status"] == "conflict"
    assert payload["current_version"] == 2
    assert "changed from version 1 to version 2" in payload["conflict_reason"]

    state = await confirmation_client.get(
        f"/v1/return-requests/{request_id}/sync-state",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert state.status_code == 200, state.text
    assert state.json()["status"] == "conflict"


@pytest.mark.asyncio
async def test_missing_evidence_capability_is_denied(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    await _grant_return_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())

    await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "missing-cap-return-request"},
        ),
        json={
            "return_request_id": request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "quantity_base": "1.000000",
                }
            ],
        },
    )

    note = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/evidence/notes",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        json={
            "evidence_id": str(uuid4()),
            "device_captured_at": "2026-08-28T12:00:00Z",
            "note_text": "Should fail.",
        },
    )
    assert note.status_code == 403, note.text
    assert note.json()["error"]["code"] == "capability_required"
