from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_payment_clearance_contract import approved_prepaid_order, auth, record_receipt
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings
from tradeflow_api.delivery_confirmation_outbox import create_draft_invoice_for_event
from tradeflow_api.object_storage import StoredObjectMetadata


class FakeObjectStorage:
    async def ensure_bucket(self) -> None:
        return None

    def signed_put_url(self, *, content_type: str, object_key: str, sha256: str) -> str:
        return f"https://objects.test/{object_key}?sha256={sha256}&type={content_type}"

    async def head(self, object_key: str) -> StoredObjectMetadata:
        del object_key
        return StoredObjectMetadata(
            content_type="image/png",
            sha256="a" * 64,
            size_bytes=12,
        )


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
) -> AsyncIterator[AsyncClient]:
    app = create_app(confirmation_settings)
    app.state.object_storage = FakeObjectStorage()
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
    postgres_url: str,
) -> None:
    fixture, delivery_id = await dispatched_prepaid_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
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
    assert upload.json()["upload_headers"] == {
        "Content-Type": "image/png",
        "x-amz-meta-sha256": "a" * 64,
    }
    completed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "verified"

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE inventory_valuation
                SET inventory_value = 15.000000,
                    moving_average_unit_cost = 7.500000
                WHERE sku_id = :sku_id
                """
            ),
            {"sku_id": fixture["sku_id"]},
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
    confirmed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "confirm-accepted-delivery"},
        ),
        json=command,
    )
    assert confirmed.status_code == 201, confirmed.text
    payload = confirmed.json()
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

    availability = await confirmation_client.get(
        "/v1/inventory/availability",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"query": "PREPAID-EA"},
    )
    assert availability.status_code == 200, availability.text
    transit = next(row for row in availability.json()["items"] if row["custody"] == "in_transit")
    assert transit["on_hand"] == "0.000000"

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        first_invoice_id = await create_draft_invoice_for_event(
            session, UUID(payload["outbox_event_id"])
        )
        replayed_invoice_id = await create_draft_invoice_for_event(
            session, UUID(payload["outbox_event_id"])
        )
        assert replayed_invoice_id == first_invoice_id
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
        "events": 1,
        "invoices": 1,
        "handler_receipts": 1,
        "approved_uninvoiced": before_exposure,
        "valuation_quantity": 0,
        "inventory_value": 0,
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
    assert dict(counts) == {"confirmations": 0, "movements": 0, "events": 0}
