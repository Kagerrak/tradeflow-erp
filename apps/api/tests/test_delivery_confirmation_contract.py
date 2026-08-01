from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import tradeflow_api.delivery_confirmation as delivery_confirmation_module
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_payment_clearance_contract import approved_prepaid_order, auth, record_receipt
from test_tracked_stock_picking_contract import (
    approved_lot_order,
    approved_serial_order,
    approved_tracked_order,
)
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings
from tradeflow_api.delivery_confirmation_outbox import (
    allocate_partial_line_amounts,
    create_draft_invoice_for_event,
    render_delivery_receipt_for_event,
)
from tradeflow_api.object_storage import StoredObjectMetadata, UploadedPart
from tradeflow_worker.worker import poll_delivery_confirmation_outbox


class FakeObjectStorage:
    def __init__(self) -> None:
        self.completed_objects: set[str] = set()
        self.computed_digest = "a" * 64
        self.fail_puts = 0
        self.head_content_type = "image/png"
        self.head_sha256 = "a" * 64
        self.head_size_bytes = 12
        self.multipart_parts = [UploadedPart(etag='"part-etag"', number=1, size_bytes=12)]
        self.put_body: bytes | None = None
        self.put_attempts = 0

    @property
    def url_expiry_seconds(self) -> int:
        return 73

    async def ensure_bucket(self) -> None:
        return None

    async def create_multipart_upload(
        self, *, content_type: str, object_key: str, sha256: str
    ) -> str:
        del content_type, object_key, sha256
        return "stable-multipart-upload"

    def signed_upload_part_url(self, *, object_key: str, part_number: int, upload_id: str) -> str:
        return f"https://objects.test/{object_key}?uploadId={upload_id}&partNumber={part_number}"

    async def list_uploaded_parts(self, *, object_key: str, upload_id: str) -> list[UploadedPart]:
        del object_key, upload_id
        return self.multipart_parts

    async def complete_multipart_upload(
        self, *, object_key: str, parts: list[UploadedPart], upload_id: str
    ) -> None:
        del parts, upload_id
        self.completed_objects.add(object_key)

    async def computed_sha256(self, object_key: str) -> str:
        if object_key not in self.completed_objects:
            raise FileNotFoundError(object_key)
        return self.computed_digest

    async def head(self, object_key: str) -> StoredObjectMetadata:
        if object_key not in self.completed_objects:
            raise FileNotFoundError(object_key)
        return StoredObjectMetadata(
            checksum_sha256="qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqo=",
            content_type=self.head_content_type,
            sha256=self.head_sha256,
            size_bytes=self.head_size_bytes,
        )

    async def put(self, *, body: bytes, content_type: str, object_key: str) -> None:
        assert content_type == "application/pdf"
        assert object_key.startswith("delivery-receipts/")
        self.put_attempts += 1
        if self.fail_puts > 0:
            self.fail_puts -= 1
            raise RuntimeError("injected document storage failure")
        self.put_body = body

    def signed_get_url(self, *, object_key: str) -> str:
        return f"https://objects.test/{object_key}"


def test_final_partial_invoice_line_receives_every_rounding_residual() -> None:
    prior = (Decimal("0"),) * 5
    allocations: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
    for _ in range(3):
        allocation = allocate_partial_line_amounts(
            quantity=Decimal("1"),
            source_quantity=Decimal("3"),
            source_subtotal=Decimal("100.00"),
            source_discount=Decimal("1.00"),
            source_tax=Decimal("12.00"),
            source_total=Decimal("111.00"),
            prior_quantity=prior[0],
            prior_subtotal=prior[1],
            prior_discount=prior[2],
            prior_tax=prior[3],
            prior_total=prior[4],
            quantum=Decimal("0.01"),
        )
        allocations.append(allocation)
        prior = tuple(
            left + right for left, right in zip(prior, (Decimal("1"), *allocation), strict=True)
        )
    assert allocations[-1] == (
        Decimal("33.34"),
        Decimal("0.34"),
        Decimal("4.00"),
        Decimal("37.00"),
    )
    assert tuple(map(sum, zip(*allocations, strict=True))) == (
        Decimal("100.00"),
        Decimal("1.00"),
        Decimal("12.00"),
        Decimal("111.00"),
    )


@pytest.fixture
def fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def confirmation_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def confirmation_client(
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
) -> AsyncIterator[AsyncClient]:
    app = create_app(confirmation_settings)
    app.state.object_storage = fake_storage
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def dispatched_prepaid_delivery(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
    *,
    payment_timing_policy: str = "prepaid",
) -> tuple[dict[str, object], str]:
    fixture = await approved_prepaid_order(
        client,
        settings,
        postgres_url,
        payment_timing_policy=payment_timing_policy,
    )
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO document_series(
                  document_series_id, branch_id, document_type, prefix, next_number
                ) VALUES (
                  :series_id, :branch_id, 'delivery_receipt', 'DR-MNL', 1
                )
                """
            ),
            {"branch_id": fixture["branch_id"], "series_id": uuid4()},
        )
        await connection.execute(
            text(
                """
                INSERT INTO stock_movements(
                  movement_id, sku_id, warehouse_id, location_id, movement_type,
                  quantity_base, unit_cost, value_delta, base_currency,
                  source_reference, entered_unit, conversion_snapshot,
                  actor_subject, correlation_id, idempotency_key,
                  movement_group_id, movement_leg)
                SELECT :movement_id, :sku_id, :warehouse_id, location_id,
                  'opening_stock', 2.000000, 0, 0, 'PHP', 'TEST-SEED', 'EA',
                  '{"source":"contract-seed","factor":"1.000000"}'::jsonb,
                  'sales-mnl', 'confirmation-contract', :idempotency_key,
                  :movement_group_id, 'opening_in'
                FROM warehouse_stock_locations
                WHERE warehouse_id = :warehouse_id AND custody = 'available'
                """
            ),
            {
                "idempotency_key": f"confirmation-opening-{uuid4()}",
                "movement_group_id": str(uuid4()),
                "movement_id": str(uuid4()),
                "sku_id": fixture["sku_id"],
                "warehouse_id": fixture["warehouse_id"],
            },
        )
    await engine.dispose()
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    if payment_timing_policy == "prepaid":
        receipt = await record_receipt(
            client,
            settings,
            fixture,
            payment_method="cash",
            key="confirmation-prepayment",
        )
        assert receipt.status_code == 201, receipt.text
    released = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "confirmation-release"},
        ),
        json={"reason": "Release fully accepted Delivery"},
    )
    assert released.status_code == 201, released.text
    expected_pick_version = 3 if payment_timing_policy == "prepaid" else 2
    pick_id = str(uuid4())
    picked = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "confirmation-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": expected_pick_version,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text
    delivery_id = str(uuid4())
    dispatched = await client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "confirmation-dispatch"},
        ),
        json={
            "delivery_id": delivery_id,
            "expected_fulfillment_version": picked.json()["version"],
            "assigned_to": "delivery-mnl",
            "pick_ids": [pick_id],
        },
    )
    assert dispatched.status_code == 201, dispatched.text
    return fixture, delivery_id


@pytest.mark.asyncio
async def test_assigned_staff_confirms_accepted_quantity_atomically_and_idempotently(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    monkeypatch: pytest.MonkeyPatch,
    postgres_url: str,
) -> None:
    fixture, delivery_id = await dispatched_prepaid_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    resumable_evidence_id = str(uuid4())
    fake_storage.multipart_parts = [
        UploadedPart(etag='"first-part"', number=1, size_bytes=5 * 1024 * 1024)
    ]
    resumable_command = {
        "evidence_id": resumable_evidence_id,
        "kind": "photo",
        "content_type": "image/jpeg",
        "size_bytes": 5 * 1024 * 1024 + 9,
        "sha256": "b" * 64,
        "device_captured_at": "2026-08-01T12:58:00Z",
    }
    resumable = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json=resumable_command,
    )
    assert resumable.status_code == 201, resumable.text
    assert resumable.json()["parts"] == [
        {
            "part_number": 2,
            "start_byte": 5 * 1024 * 1024,
            "end_byte": 5 * 1024 * 1024 + 9,
            "upload_url": (
                f"https://objects.test/deliveries/{delivery_id}/evidence/"
                f"{resumable_evidence_id}?uploadId=stable-multipart-upload&partNumber=2"
            ),
            "upload_headers": {},
        }
    ]
    resumed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json=resumable_command,
    )
    assert resumed.status_code == 200, resumed.text
    assert {key: value for key, value in resumed.json().items() if key != "expires_at"} == {
        key: value for key, value in resumable.json().items() if key != "expires_at"
    }

    async def rejected_evidence(
        label: str,
        *,
        parts: list[UploadedPart],
        content_type: str = "image/png",
        size_bytes: int = 12,
        metadata_sha256: str = "a" * 64,
        computed_digest: str = "a" * 64,
    ) -> str:
        fake_storage.multipart_parts = parts
        fake_storage.head_content_type = content_type
        fake_storage.head_size_bytes = size_bytes
        fake_storage.head_sha256 = metadata_sha256
        fake_storage.computed_digest = computed_digest
        rejected_id = str(uuid4())
        intent = await confirmation_client.post(
            f"/v1/deliveries/{delivery_id}/evidence/uploads",
            headers=auth(confirmation_settings, "delivery-mnl"),
            json={
                "evidence_id": rejected_id,
                "kind": "photo",
                "content_type": "image/png",
                "size_bytes": 12,
                "sha256": "a" * 64,
                "device_captured_at": "2026-08-01T12:58:30Z",
            },
        )
        assert intent.status_code == 201, intent.text
        rejected = await confirmation_client.post(
            f"/v1/deliveries/{delivery_id}/evidence/{rejected_id}/complete",
            headers=auth(confirmation_settings, "delivery-mnl"),
        )
        assert rejected.status_code == 409, f"{label}: {rejected.text}"
        return rejected.json()["error"]["code"]

    assert (
        await rejected_evidence("missing part", parts=[]) == "delivery_evidence_upload_incomplete"
    )
    assert (
        await rejected_evidence(
            "wrong part size",
            parts=[UploadedPart(etag='"short"', number=1, size_bytes=11)],
        )
        == "delivery_evidence_upload_incomplete"
    )
    matching_part = [UploadedPart(etag='"part"', number=1, size_bytes=12)]
    assert (
        await rejected_evidence(
            "wrong MIME",
            parts=matching_part,
            content_type="image/jpeg",
        )
        == "delivery_evidence_integrity_conflict"
    )
    assert (
        await rejected_evidence("wrong size", parts=matching_part, size_bytes=11)
        == "delivery_evidence_integrity_conflict"
    )
    assert (
        await rejected_evidence(
            "wrong digest",
            parts=matching_part,
            computed_digest="b" * 64,
        )
        == "delivery_evidence_integrity_conflict"
    )
    fake_storage.head_content_type = "image/png"
    fake_storage.head_size_bytes = 12
    fake_storage.head_sha256 = "a" * 64
    fake_storage.computed_digest = "a" * 64
    fake_storage.multipart_parts = [UploadedPart(etag='"part-etag"', number=1, size_bytes=12)]
    evidence_id = str(uuid4())
    upload = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T12:59:00Z",
        },
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["status"] == "uploading"
    assert upload.json()["upload_id"] == "stable-multipart-upload"
    assert upload.json()["part_size"] == 5 * 1024 * 1024
    assert upload.json()["parts"] == []
    upload_expiry = datetime.fromisoformat(upload.json()["expires_at"])
    assert 65 <= (upload_expiry - datetime.now(UTC)).total_seconds() <= 73
    changed_capture_time = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T12:59:01Z",
        },
    )
    assert changed_capture_time.status_code == 409, changed_capture_time.text
    assert changed_capture_time.json()["error"]["code"] == "delivery_evidence_identity_conflict"
    completed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "verified"
    evidence_access = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/access",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert evidence_access.status_code == 200, evidence_access.text
    assert (
        f"deliveries/{delivery_id}/evidence/{evidence_id}" in evidence_access.json()["access_url"]
    )
    access_expiry = datetime.fromisoformat(evidence_access.json()["expires_at"])
    assert 65 <= (access_expiry - datetime.now(UTC)).total_seconds() <= 73
    denied_access = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/access",
        headers=auth(confirmation_settings, "delivery-backup-mnl"),
    )
    assert denied_access.status_code == 403, denied_access.text
    assert denied_access.json()["error"]["code"] == "delivery_assignment_required"

    administrator = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "ops-admin"),
        json={
            "evidence_id": str(uuid4()),
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T12:59:00Z",
        },
    )
    assert administrator.status_code == 403, administrator.text
    assert administrator.json()["error"]["code"] == "capability_required"

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE delivery_state SET assigned_to = 'delivery-backup-mnl' "
                "WHERE delivery_id = :delivery_id"
            ),
            {"delivery_id": delivery_id},
        )
        await connection.execute(
            text("DELETE FROM user_branch_scopes WHERE user_subject = 'delivery-backup-mnl'")
        )
        await connection.execute(
            text(
                "INSERT INTO user_branch_scopes(user_subject, branch_id) "
                "SELECT 'delivery-backup-mnl', branch_id FROM branches WHERE code = 'CEB'"
            )
        )
    wrong_scope = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-backup-mnl"),
        json={
            "evidence_id": str(uuid4()),
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T12:59:00Z",
        },
    )
    assert wrong_scope.status_code == 403, wrong_scope.text
    assert wrong_scope.json()["error"]["code"] == "operational_scope_required"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE delivery_state SET assigned_to = 'delivery-mnl' "
                "WHERE delivery_id = :delivery_id"
            ),
            {"delivery_id": delivery_id},
        )
        await connection.execute(
            text("DELETE FROM user_branch_scopes WHERE user_subject = 'delivery-backup-mnl'")
        )
        await connection.execute(
            text(
                "INSERT INTO user_branch_scopes(user_subject, branch_id) "
                "VALUES ('delivery-backup-mnl', :branch_id)"
            ),
            {"branch_id": fixture["branch_id"]},
        )
    await engine.dispose()

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO stock_movements(
                  movement_id, sku_id, warehouse_id, location_id, movement_type,
                  quantity_base, unit_cost, value_delta, base_currency,
                  source_reference, entered_unit, conversion_snapshot,
                  actor_subject, correlation_id, idempotency_key,
                  movement_group_id, movement_leg)
                SELECT :movement_id, :sku_id, :warehouse_id, location_id,
                  'opening_stock', 2.000000, 15.000000, 30.000000, 'PHP',
                  'CONFIRMATION-COST-SNAPSHOT', 'EA',
                  '{"source":"contract-replenishment","factor":"1.000000"}'::jsonb,
                  'sales-mnl', 'confirmation-contract', :idempotency_key,
                  :movement_group_id, 'opening_in'
                FROM warehouse_stock_locations
                WHERE warehouse_id = :warehouse_id AND custody = 'available'
                """
            ),
            {
                "idempotency_key": f"confirmation-replenishment-{uuid4()}",
                "movement_group_id": str(uuid4()),
                "movement_id": str(uuid4()),
                "sku_id": fixture["sku_id"],
                "warehouse_id": fixture["warehouse_id"],
            },
        )
        await connection.execute(
            text(
                """
                UPDATE inventory_availability
                SET on_hand = on_hand + 2.000000
                WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                  AND identity_key = '' AND location_id IN (
                    SELECT location_id FROM warehouse_stock_locations
                    WHERE custody = 'available'
                  )
                """
            ),
            {
                "sku_id": fixture["sku_id"],
                "warehouse_id": fixture["warehouse_id"],
            },
        )
        await connection.execute(
            text(
                """
                UPDATE inventory_valuation
                SET quantity_on_hand = quantity_on_hand + 2.000000,
                    inventory_value = inventory_value + 30.000000,
                    moving_average_unit_cost = 7.500000
                WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                """
            ),
            {
                "sku_id": fixture["sku_id"],
                "warehouse_id": fixture["warehouse_id"],
            },
        )
    await engine.dispose()

    confirmation_id = str(uuid4())
    command = {
        "confirmation_id": confirmation_id,
        "expected_delivery_version": 1,
        "recipient_name": "Ana Santos",
        "device_captured_at": "2026-08-01T13:00:00Z",
        "notes": "Two sealed cartons accepted.",
        "evidence_ids": [evidence_id],
        "lines": [
            {
                "line_id": fixture["line_id"],
                "accepted_quantity_base": "2.000000",
            }
        ],
    }

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM user_branch_scopes WHERE user_subject = 'delivery-mnl'")
        )
        await connection.execute(
            text(
                "INSERT INTO user_branch_scopes(user_subject, branch_id) "
                "SELECT 'delivery-mnl', branch_id FROM branches WHERE code = 'CEB'"
            )
        )
    revoked_scope = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "confirmation-revoked-scope"},
        ),
        json={**command, "confirmation_id": str(uuid4())},
    )
    assert revoked_scope.status_code == 403, revoked_scope.text
    assert revoked_scope.json()["error"]["code"] == "operational_scope_required"
    async with engine.begin() as connection:
        written = await connection.scalar(text("SELECT count(*) FROM delivery_confirmations"))
        await connection.execute(
            text("DELETE FROM user_branch_scopes WHERE user_subject = 'delivery-mnl'")
        )
        await connection.execute(
            text(
                "INSERT INTO user_branch_scopes(user_subject, branch_id) "
                "VALUES ('delivery-mnl', :branch_id)"
            ),
            {"branch_id": fixture["branch_id"]},
        )
    await engine.dispose()
    assert written == 0

    reassigned = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-backup-mnl",
            **{"Idempotency-Key": "confirmation-reassigned"},
        ),
        json=command,
    )
    assert reassigned.status_code == 403, reassigned.text
    assert reassigned.json()["error"]["code"] == "delivery_assignment_required"

    quantity_mismatch = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "confirmation-quantity-mismatch"},
        ),
        json={
            **command,
            "confirmation_id": str(uuid4()),
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "1.000000",
                }
            ],
        },
    )
    assert quantity_mismatch.status_code == 409, quantity_mismatch.text
    assert quantity_mismatch.json()["error"]["code"] == "delivery_quantity_conflict"

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE skus SET tracking_policy = 'lot' WHERE sku_id = :sku_id"),
            {"sku_id": fixture["sku_id"]},
        )
    tracking_changed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "confirmation-tracking-changed"},
        ),
        json={**command, "confirmation_id": str(uuid4())},
    )
    assert tracking_changed.status_code == 409, tracking_changed.text
    assert tracking_changed.json()["error"]["code"] == "delivery_tracking_policy_conflict"
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE skus SET tracking_policy = 'untracked' WHERE sku_id = :sku_id"),
            {"sku_id": fixture["sku_id"]},
        )
    await engine.dispose()

    original_store = delivery_confirmation_module.store_command_result

    async def fail_after_writes(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected failure after authoritative writes")

    monkeypatch.setattr(delivery_confirmation_module, "store_command_result", fail_after_writes)
    with pytest.raises(RuntimeError, match="injected failure"):
        await confirmation_client.post(
            f"/v1/deliveries/{delivery_id}/confirmations",
            headers=auth(
                confirmation_settings,
                "delivery-mnl",
                **{"Idempotency-Key": "confirm-accepted-delivery"},
            ),
            json=command,
        )
    monkeypatch.setattr(delivery_confirmation_module, "store_command_result", original_store)
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        rolled_back = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT
                      (SELECT count(*) FROM delivery_confirmations) AS confirmations,
                      (SELECT count(*) FROM delivery_receipts) AS receipts,
                      (SELECT count(*) FROM document_series_number_audit) AS number_audit,
                      (SELECT count(*) FROM outbox_events) AS events,
                      (SELECT count(*) FROM stock_movements
                       WHERE movement_type = 'delivery_confirmation') AS movements
                    """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(rolled_back) == {
        "confirmations": 0,
        "receipts": 0,
        "number_audit": 0,
        "events": 0,
        "movements": 0,
    }

    first, concurrent_replay = await asyncio.gather(
        *[
            confirmation_client.post(
                f"/v1/deliveries/{delivery_id}/confirmations",
                headers=auth(
                    confirmation_settings,
                    "delivery-mnl",
                    **{"Idempotency-Key": "confirm-accepted-delivery"},
                ),
                json=command,
            )
            for _ in range(2)
        ]
    )
    assert sorted([first.status_code, concurrent_replay.status_code]) == [200, 201]
    assert first.json() == concurrent_replay.json()
    payload = first.json()
    assert payload["confirmation_id"] == confirmation_id
    assert payload["delivery_id"] == delivery_id
    assert payload["status"] == "confirmed"
    assert payload["version"] == 2
    assert payload["lines"] == [
        {
            "line_id": fixture["line_id"],
            "sku_id": fixture["sku_id"],
            "accepted_quantity_base": "2.000000",
            "unit_cost": "7.500000",
            "value_delta": "-15.000000",
            "outbound_movement_id": payload["lines"][0]["outbound_movement_id"],
        }
    ]
    assert payload["delivery_receipt"]["number"].startswith("DR-MNL-")
    assert payload["delivery_receipt"]["status"] == "pending_document"
    assert payload["outbox_event_id"]

    changed_replay = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "confirm-accepted-delivery"},
        ),
        json={**command, "notes": "Changed after commit"},
    )
    assert changed_replay.status_code == 409, changed_replay.text
    assert changed_replay.json()["error"]["code"] == "idempotency_conflict"

    stale = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "confirmation-stale-version"},
        ),
        json={**command, "confirmation_id": str(uuid4())},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "delivery_version_conflict"

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        before_exposure = await connection.scalar(
            text(
                "SELECT approved_uninvoiced FROM customer_credit_exposure "
                "WHERE customer_id = :customer_id"
            ),
            {"customer_id": fixture["customer_id"]},
        )
    await engine.dispose()

    replay = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "confirm-accepted-delivery"},
        ),
        json=command,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == payload

    assigned = await confirmation_client.get(
        f"/v1/deliveries/{delivery_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "confirmed"
    assert assigned.json()["version"] == 2
    fulfillment = await confirmation_client.get(
        "/v1/fulfillment/orders",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"sales_order_id": fixture["sales_order_id"]},
    )
    assert fulfillment.status_code == 200, fulfillment.text
    assert fulfillment.json()["items"][0]["status"] == "delivered"

    availability = await confirmation_client.get(
        "/v1/inventory/availability",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"query": "PREPAID-EA"},
    )
    assert availability.status_code == 200, availability.text
    transit = next(row for row in availability.json()["items"] if row["custody"] == "in_transit")
    assert transit["on_hand"] == "0.000000"

    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text

    engine = create_async_engine(postgres_url)
    poison_event_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO outbox_events (
                  outbox_event_id, aggregate_type, aggregate_id, event_type,
                  payload, correlation_id
                ) VALUES (
                  :event_id, 'DeliveryConfirmation', :aggregate_id,
                  'delivery.confirmed.v1',
                  CAST(:payload AS jsonb), 'poison-event-test'
                )
                """
            ),
            {
                "aggregate_id": uuid4(),
                "event_id": poison_event_id,
                "payload": '{"confirmation_id":"00000000-0000-0000-0000-000000000000"}',
            },
        )
        await connection.execute(
            text("INSERT INTO outbox_processing_state (outbox_event_id) VALUES (:event_id)"),
            {"event_id": poison_event_id},
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    fake_storage.fail_puts = 1
    first_poll = await poll_delivery_confirmation_outbox(
        {
            "database_session_factory": factory,
            "object_storage": fake_storage,
        }
    )
    assert first_poll == {"completed": 0, "failed": 2}
    async with engine.begin() as connection:
        unavailable = await connection.scalar(
            text(
                "SELECT status FROM delivery_receipt_documents "
                "WHERE delivery_receipt_id = :receipt_id"
            ),
            {"receipt_id": payload["delivery_receipt"]["delivery_receipt_id"]},
        )
        handler_count = await connection.scalar(
            text("SELECT count(*) FROM outbox_handler_receipts")
        )
        invoice_count = await connection.scalar(text("SELECT count(*) FROM draft_invoices"))
        await connection.execute(
            text("UPDATE outbox_processing_state SET available_at = now() WHERE status = 'failed'")
        )
    assert unavailable == "unavailable"
    assert handler_count == 0
    assert invoice_count == 0
    second_poll = await poll_delivery_confirmation_outbox(
        {
            "database_session_factory": factory,
            "object_storage": fake_storage,
        }
    )
    assert second_poll == {"completed": 1, "failed": 1}
    assert fake_storage.put_attempts == 2
    async with engine.connect() as connection:
        processing = {
            row.outbox_event_id: (row.status, row.attempts)
            for row in (
                await connection.execute(
                    text("SELECT outbox_event_id, status, attempts FROM outbox_processing_state")
                )
            )
        }
    assert processing[UUID(payload["outbox_event_id"])] == ("completed", 2)
    assert processing[poison_event_id] == ("failed", 2)

    async with engine.begin() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        first_invoice_id = await create_draft_invoice_for_event(
            session, UUID(payload["outbox_event_id"])
        )
        replayed_invoice_id = await create_draft_invoice_for_event(
            session, UUID(payload["outbox_event_id"])
        )
        assert replayed_invoice_id == first_invoice_id
        receipt_id = await render_delivery_receipt_for_event(
            session,
            UUID(payload["outbox_event_id"]),
            fake_storage,
        )
        replayed_receipt_id = await render_delivery_receipt_for_event(
            session,
            UUID(payload["outbox_event_id"]),
            fake_storage,
        )
        assert replayed_receipt_id == receipt_id
    assert fake_storage.put_body is not None
    rendered = fake_storage.put_body.decode("latin-1")
    assert "TradeFlow Delivery Receipt" in rendered
    assert "PREPAID-EA" in rendered
    assert "Ana Santos" in rendered

    receipt = await confirmation_client.get(
        f"/v1/delivery-receipts/{payload['delivery_receipt']['delivery_receipt_id']}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "ready"
    snapshot = receipt.json()["snapshot"]
    assert snapshot["customer_account_number"]
    assert snapshot["customer_legal_name"] == "Prepaid Retail Customer"
    assert snapshot["delivery_address"]["line_1"] == "100 Payment Street"
    assert f"Customer: {snapshot['customer_account_number']} - Prepaid Retail Customer" in rendered
    assert "Delivery address: 100 Payment Street, Manila, NCR, 1000" in rendered
    assert f"Sales Order: {fixture['sales_order_id']}" in rendered
    assert f"Source line: {fixture['line_id']}" in rendered
    access = await confirmation_client.post(
        f"/v1/delivery-receipts/{payload['delivery_receipt']['delivery_receipt_id']}/access",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert access.status_code == 200, access.text
    assert access.json()["access_url"].endswith(
        f"delivery-receipts/{payload['delivery_receipt']['delivery_receipt_id']}.pdf"
    )
    receipt_access_expiry = datetime.fromisoformat(access.json()["expires_at"])
    assert 65 <= (receipt_access_expiry - datetime.now(UTC)).total_seconds() <= 73
    denied_receipt = await confirmation_client.post(
        f"/v1/delivery-receipts/{payload['delivery_receipt']['delivery_receipt_id']}/access",
        headers=auth(confirmation_settings, "delivery-backup-mnl"),
    )
    assert denied_receipt.status_code == 403, denied_receipt.text
    assert denied_receipt.json()["error"]["code"] == "delivery_assignment_required"
    async with engine.begin() as connection:
        series_id = await connection.scalar(
            text(
                "SELECT document_series_id FROM delivery_receipts "
                "WHERE delivery_receipt_id = :receipt_id"
            ),
            {"receipt_id": payload["delivery_receipt"]["delivery_receipt_id"]},
        )
        await connection.execute(
            text(
                """
                INSERT INTO document_series_number_audit(
                  document_series_number_audit_id, document_series_id,
                  series_number, status, reason
                ) VALUES (:audit_id, :series_id, 2, 'skipped', :reason)
                """
            ),
            {
                "audit_id": uuid4(),
                "reason": "Printer stock damaged before allocation",
                "series_id": series_id,
            },
        )
        await connection.execute(
            text(
                "UPDATE document_series SET next_number = next_number + 1 "
                "WHERE document_series_id = :series_id"
            ),
            {"series_id": series_id},
        )
    with pytest.raises(DBAPIError, match="immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE delivery_receipts SET snapshot = '{}'::jsonb "
                    "WHERE delivery_receipt_id = :receipt_id"
                ),
                {"receipt_id": payload["delivery_receipt"]["delivery_receipt_id"]},
            )
    with pytest.raises(DBAPIError, match="sequence is monotonic"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE document_series SET next_number = next_number + 2 "
                    "WHERE document_series_id = :series_id"
                ),
                {"series_id": series_id},
            )
    async with engine.connect() as connection:
        artifacts = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT
                      (SELECT count(*) FROM delivery_confirmations) AS confirmations,
                      (SELECT count(*) FROM delivery_confirmation_lines) AS positions,
                      (SELECT count(*) FROM delivery_receipts) AS receipts,
                      (SELECT count(*) FROM document_series_number_audit) AS number_audit,
                      (SELECT count(*) FROM outbox_events) AS events,
                      (SELECT count(*) FROM draft_invoices) AS invoices,
                      (SELECT count(*) FROM outbox_handler_receipts) AS handler_receipts,
                      (SELECT approved_uninvoiced FROM customer_credit_exposure
                       WHERE customer_id = :customer_id) AS approved_uninvoiced,
                      (SELECT quantity_on_hand FROM inventory_valuation
                       WHERE sku_id = :sku_id) AS valuation_quantity,
                      (SELECT inventory_value FROM inventory_valuation
                       WHERE sku_id = :sku_id) AS inventory_value
                    """
                    ),
                    {
                        "customer_id": fixture["customer_id"],
                        "sku_id": fixture["sku_id"],
                    },
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(artifacts) == {
        "confirmations": 1,
        "positions": 1,
        "receipts": 1,
        "number_audit": 2,
        "events": 2,
        "invoices": 1,
        "handler_receipts": 2,
        "approved_uninvoiced": before_exposure,
        "valuation_quantity": 2,
        "inventory_value": 15,
    }


@pytest.mark.asyncio
async def test_cash_on_delivery_confirmation_waits_for_atomic_collection_slice(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture, delivery_id = await dispatched_prepaid_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
        payment_timing_policy="cash_on_delivery",
    )
    rejected = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "cod-confirmation-before-collection"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "expected_delivery_version": 1,
            "recipient_name": "Ana Santos",
            "device_captured_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [str(uuid4())],
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "cod_collection_required"


@pytest.mark.asyncio
async def test_case_delivery_allocates_invoice_and_receipt_in_approved_units(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    fixture = await approved_tracked_order(
        confirmation_client,
        confirmation_settings,
        tracking_policy="untracked",
        expiration_control=False,
        key_prefix="case-confirmation",
        conversions=[
            {
                "unit_code": "CASE",
                "base_quantity": "12.000000",
                "effective_from": "2026-01-01",
                "effective_to": None,
            }
        ],
        selling_unit="CASE",
        order_quantity="2.000000",
        stock_entries=[{"quantity": "24.000000"}],
    )
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    picked = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "case-confirmation-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "0.500000",
                    "unit_code": "CASE",
                    "selections": [],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text
    delivery_id = str(uuid4())
    dispatched = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "case-confirmation-dispatch"},
        ),
        json={
            "delivery_id": delivery_id,
            "expected_fulfillment_version": picked.json()["version"],
            "assigned_to": "delivery-mnl",
            "pick_ids": [pick_id],
        },
    )
    assert dispatched.status_code == 201, dispatched.text
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO document_series(document_series_id, branch_id, document_type, "
                "prefix, next_number) VALUES (:id, :branch_id, 'delivery_receipt', "
                "'DR-MNL', 1)"
            ),
            {"branch_id": fixture["branch_id"], "id": uuid4()},
        )
        exposure_before = await connection.scalar(
            text(
                "SELECT approved_uninvoiced FROM customer_credit_exposure "
                "WHERE customer_id = :customer_id"
            ),
            {"customer_id": fixture["customer_id"]},
        )
    evidence_id = str(uuid4())
    uploaded = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    completed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    confirmed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "case-delivery-confirmation"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "device_captured_at": "2026-08-01T13:01:00Z",
            "evidence_ids": [evidence_id],
            "expected_delivery_version": 1,
            "lines": [
                {
                    "accepted_quantity_base": "6.000000",
                    "line_id": fixture["line_id"],
                }
            ],
            "recipient_name": "Case Recipient",
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    fulfillment = await confirmation_client.get(
        "/v1/fulfillment/orders",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"sales_order_id": fixture["sales_order_id"]},
    )
    assert fulfillment.status_code == 200, fulfillment.text
    assert fulfillment.json()["items"][0]["status"] == "partially_delivered"

    event_id = UUID(confirmed.json()["outbox_event_id"])
    async with engine.begin() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        invoice_id = await create_draft_invoice_for_event(session, event_id)
        assert await create_draft_invoice_for_event(session, event_id) == invoice_id
        receipt_id = await render_delivery_receipt_for_event(session, event_id, fake_storage)
        assert (
            await render_delivery_receipt_for_event(session, event_id, fake_storage) == receipt_id
        )
    async with engine.connect() as connection:
        invoice = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT i.status, i.subtotal, i.discount_total, i.tax_total,
                               i.grand_total, i.source_snapshot,
                               l.accepted_quantity_base, l.unit_price,
                               l.subtotal AS line_subtotal,
                               l.discount_amount, l.tax_amount, l.line_total,
                               e.approved_uninvoiced
                        FROM draft_invoices i
                        JOIN draft_invoice_lines l USING (draft_invoice_id)
                        LEFT JOIN customer_credit_exposure e USING (customer_id)
                        WHERE i.draft_invoice_id = :invoice_id
                        """
                    ),
                    {"invoice_id": invoice_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert invoice["status"] == "draft"
    assert invoice["accepted_quantity_base"] == Decimal("6.000000")
    assert invoice["unit_price"] == Decimal("8.333333")
    assert invoice["line_subtotal"] == Decimal("50.000000")
    assert invoice["discount_amount"] == Decimal("0.000000")
    assert invoice["tax_amount"] == Decimal("6.000000")
    assert invoice["line_total"] == Decimal("56.000000")
    assert invoice["subtotal"] == Decimal("50.000000")
    assert invoice["discount_total"] == Decimal("0.000000")
    assert invoice["tax_total"] == Decimal("6.000000")
    assert invoice["grand_total"] == Decimal("56.000000")
    assert invoice["approved_uninvoiced"] == exposure_before
    assert invoice["source_snapshot"] == {
        "delivery_confirmation_id": confirmed.json()["confirmation_id"],
        "outbox_event_id": confirmed.json()["outbox_event_id"],
    }
    receipt = await confirmation_client.get(
        f"/v1/delivery-receipts/{receipt_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert receipt.status_code == 200, receipt.text
    line = receipt.json()["snapshot"]["lines"][0]
    assert line["accepted_quantity_base"] == "6.000000"
    assert line["accepted_quantity_entered"] == "0.500000"
    assert line["entered_unit"] == "CASE"
    assert fake_storage.put_body is not None
    rendered = fake_storage.put_body.decode("latin-1")
    assert "0.500000 CASE" in rendered
    assert "6.000000 CASE" not in rendered


@pytest.mark.asyncio
async def test_confirmation_preserves_exact_current_serial_identity_custody(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_serial_order(confirmation_client, confirmation_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    picked = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "serial-confirmation-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [
                        {
                            "serial_number": serial,
                            "quantity": "1.000000",
                            "manual_reason": "Serial label confirmed",
                        }
                        for serial in ("SN-001", "SN-002")
                    ],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text
    delivery_id = str(uuid4())
    dispatched = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "serial-confirmation-dispatch"},
        ),
        json={
            "delivery_id": delivery_id,
            "expected_fulfillment_version": picked.json()["version"],
            "assigned_to": "delivery-mnl",
            "pick_ids": [pick_id],
        },
    )
    assert dispatched.status_code == 201, dispatched.text
    assert dispatched.json()["lines"][0]["serial_numbers"] == ["SN-001", "SN-002"]
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO document_series(document_series_id, branch_id, document_type, "
                "prefix, next_number) VALUES (:id, :branch_id, 'delivery_receipt', "
                "'DR-MNL', 1)"
            ),
            {"branch_id": fixture["branch_id"], "id": uuid4()},
        )
    await engine.dispose()
    evidence_id = str(uuid4())
    upload = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert upload.status_code == 201, upload.text
    completed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    confirmed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "serial-confirmation"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "device_captured_at": "2026-08-01T13:01:00Z",
            "evidence_ids": [evidence_id],
            "expected_delivery_version": 1,
            "lines": [
                {
                    "accepted_quantity_base": "2.000000",
                    "line_id": fixture["line_id"],
                }
            ],
            "recipient_name": "Serial Recipient",
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    availability = await confirmation_client.get(
        "/v1/inventory/availability",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"query": "SERIAL-PICK-EA"},
    )
    transit = [
        row
        for row in availability.json()["items"]
        if row["custody"] == "in_transit" and row["on_hand"] != "0.000000"
    ]
    assert transit == []
    availability_before_rebuild = availability.json()
    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    availability_after_rebuild = await confirmation_client.get(
        "/v1/inventory/availability",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"query": "SERIAL-PICK-EA"},
    )
    assert availability_after_rebuild.status_code == 200, availability_after_rebuild.text
    assert availability_after_rebuild.json() == availability_before_rebuild

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        counts = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT
                      (SELECT count(*) FROM delivery_confirmations) AS confirmations,
                      (SELECT count(*) FROM stock_movements
                       WHERE movement_type = 'delivery_confirmation') AS movements,
                      (SELECT count(*) FROM outbox_events) AS events
                    """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(counts) == {"confirmations": 1, "movements": 1, "events": 1}


@pytest.mark.asyncio
async def test_confirmation_preserves_lot_identity_custody_through_rebuild(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_lot_order(confirmation_client, confirmation_settings)
    fulfillment_order_id = fixture["fulfillment_order"]["fulfillment_order_id"]
    pick_id = str(uuid4())
    picked = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "lot-confirmation-pick"},
        ),
        json={
            "pick_id": pick_id,
            "expected_fulfillment_version": 3,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "quantity": "2.000000",
                    "unit_code": "EA",
                    "selections": [
                        {
                            "lot_code": lot_code,
                            "quantity": "1.000000",
                            "manual_reason": "Lot label confirmed",
                        }
                        for lot_code in ("LOT-EARLY", "LOT-LATE")
                    ],
                }
            ],
        },
    )
    assert picked.status_code == 201, picked.text
    assert {
        lot["lot_code"] for line in picked.json()["lines"] for lot in line["lot_selections"]
    } == {
        "LOT-EARLY",
        "LOT-LATE",
    }
    delivery_id = str(uuid4())
    dispatched = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "lot-confirmation-dispatch"},
        ),
        json={
            "delivery_id": delivery_id,
            "expected_fulfillment_version": picked.json()["version"],
            "assigned_to": "delivery-mnl",
            "pick_ids": [pick_id],
        },
    )
    assert dispatched.status_code == 201, dispatched.text
    assert {
        lot["lot_code"] for line in dispatched.json()["lines"] for lot in line["lot_selections"]
    } == {
        "LOT-EARLY",
        "LOT-LATE",
    }
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO document_series(document_series_id, branch_id, document_type, "
                "prefix, next_number) VALUES (:id, :branch_id, 'delivery_receipt', "
                "'DR-MNL', 1)"
            ),
            {"branch_id": fixture["branch_id"], "id": uuid4()},
        )
    await engine.dispose()
    evidence_id = str(uuid4())
    uploaded = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": "signature",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    completed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    confirmed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "lot-delivery-confirmation"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "device_captured_at": "2026-08-01T13:01:00Z",
            "evidence_ids": [evidence_id],
            "expected_delivery_version": 1,
            "lines": [
                {
                    "accepted_quantity_base": "2.000000",
                    "line_id": fixture["line_id"],
                }
            ],
            "recipient_name": "Lot Recipient",
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    availability = await confirmation_client.get(
        "/v1/inventory/availability",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"query": "LOT-PICK-EA"},
    )
    assert availability.status_code == 200, availability.text
    assert not [
        row
        for row in availability.json()["items"]
        if row["custody"] == "in_transit" and row["on_hand"] != "0.000000"
    ]
    before_rebuild = availability.json()
    rebuilt = await confirmation_client.post(
        "/v1/inventory/projections/rebuild",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert rebuilt.status_code == 200, rebuilt.text
    after_rebuild = await confirmation_client.get(
        "/v1/inventory/availability",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"query": "LOT-PICK-EA"},
    )
    assert after_rebuild.status_code == 200, after_rebuild.text
    assert after_rebuild.json() == before_rebuild
