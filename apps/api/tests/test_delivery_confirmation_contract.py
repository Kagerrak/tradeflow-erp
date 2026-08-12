from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import tradeflow_api.delivery_confirmation as delivery_confirmation_module
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_payment_clearance_contract import (
    approved_prepaid_order,
    auth,
    bootstrap_payment_clearance,
    create_customer,
    create_sku,
    create_tax_code,
    record_receipt,
    seed_available_stock,
)
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


@pytest.mark.asyncio
async def test_cod_residual_matches_each_invoice_when_outbox_runs_out_of_order(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    organization = await bootstrap_payment_clearance(
        confirmation_client,
        confirmation_settings,
    )
    branch = organization["branches"][0]
    branch_id = branch["branch_id"]
    warehouse_id = branch["warehouses"][0]["warehouse_id"]
    customer = await create_customer(
        confirmation_client,
        confirmation_settings,
        branch_id,
        payment_timing_policy="cash_on_delivery",
    )
    sku = await create_sku(confirmation_client, confirmation_settings)
    tax = await create_tax_code(confirmation_client, confirmation_settings)
    price_list_response = await confirmation_client.post(
        "/v1/sales/price-list-versions",
        headers=auth(
            confirmation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "cod-residual-price-list"},
        ),
        json={
            "code": "MNL-COD-RESIDUAL",
            "branch_id": branch_id,
            "customer_id": customer["customer_id"],
            "inclusion_mode": "exclusive",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "items": [
                {
                    "sku_id": sku["sku_id"],
                    "unit_code": "EA",
                    "list_unit_price": "0.013333",
                    "floor_unit_price": "0.013333",
                    "tax_code_version_id": tax["tax_code_version_id"],
                }
            ],
        },
    )
    assert price_list_response.status_code == 201, price_list_response.text
    price_list = price_list_response.json()
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO document_series(document_series_id, branch_id, document_type, "
                "prefix, next_number) VALUES (:id, :branch_id, 'delivery_receipt', "
                "'DR-MNL', 1)"
            ),
            {"branch_id": branch_id, "id": uuid4()},
        )
    await engine.dispose()

    sales_order_id = str(uuid4())
    line_id = str(uuid4())
    created = await confirmation_client.post(
        "/v1/sales/orders",
        headers=auth(
            confirmation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "cod-residual-order"},
        ),
        json={
            "sales_order_id": sales_order_id,
            "branch_id": branch_id,
            "customer_id": customer["customer_id"],
            "expected_customer_version": customer["version"],
            "expected_price_list_version_id": price_list["price_list_version_id"],
            "expected_pricing_date": date.today().isoformat(),
            "delivery_address_version_id": customer["addresses"][0]["address_version_id"],
            "payment_timing_policy": None,
            "payment_timing_override_reason": None,
            "order_discount_amount": "0.000000",
            "lines": [
                {
                    "line_id": line_id,
                    "sku_id": sku["sku_id"],
                    "expected_price_list_line_id": price_list["items"][0]["price_list_line_id"],
                    "expected_unit_conversion_id": None,
                    "expected_unit_conversion_version": None,
                    "quantity": "3.000000",
                    "unit_code": "EA",
                    "manual_override_unit_price": None,
                    "price_override_reason": None,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["grand_total"] == "0.04"
    await seed_available_stock(
        postgres_url,
        sku_id=sku["sku_id"],
        warehouse_id=warehouse_id,
        quantity="3.000000",
    )
    approved = await confirmation_client.post(
        f"/v1/sales/orders/{sales_order_id}/commercial-approval",
        headers=auth(
            confirmation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "cod-residual-approval", "If-Match": "1"},
        ),
        json={
            "warehouse_id": warehouse_id,
            "exception_reason": None,
            "credit_override_reason": None,
        },
    )
    assert approved.status_code == 201, approved.text
    fulfillment = await confirmation_client.get(
        "/v1/fulfillment/orders",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
        params={"sales_order_id": sales_order_id},
    )
    assert fulfillment.status_code == 200, fulfillment.text
    fulfillment_order_id = fulfillment.json()["items"][0]["fulfillment_order_id"]
    released = await confirmation_client.post(
        f"/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "cod-residual-release"},
        ),
        json={"reason": "Split fractional COD value across three deliveries"},
    )
    assert released.status_code == 201, released.text

    version = 2
    pick_ids: list[str] = []
    for index in range(3):
        pick_id = str(uuid4())
        picked = await confirmation_client.post(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/picks",
            headers=auth(
                confirmation_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": f"cod-residual-pick-{index}"},
            ),
            json={
                "pick_id": pick_id,
                "expected_fulfillment_version": version,
                "lines": [
                    {
                        "line_id": line_id,
                        "quantity": "1.000000",
                        "unit_code": "EA",
                        "selections": [],
                    }
                ],
            },
        )
        assert picked.status_code == 201, picked.text
        version = picked.json()["version"]
        pick_ids.append(pick_id)

    deliveries: list[str] = []
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        version = await connection.scalar(
            text(
                "SELECT version FROM fulfillment_order_state "
                "WHERE fulfillment_order_id = :fulfillment_order_id"
            ),
            {"fulfillment_order_id": fulfillment_order_id},
        )
    await engine.dispose()
    assert version is not None
    for index, pick_id in enumerate(pick_ids):
        delivery_id = str(uuid4())
        dispatched = await confirmation_client.post(
            f"/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
            headers=auth(
                confirmation_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": f"cod-residual-dispatch-{index}"},
            ),
            json={
                "delivery_id": delivery_id,
                "expected_fulfillment_version": version,
                "assigned_to": "delivery-mnl",
                "pick_ids": [pick_id],
            },
        )
        assert dispatched.status_code == 201, dispatched.text
        version += 1
        deliveries.append(delivery_id)

    expected_amounts = ["0.01", "0.01", "0.02"]
    event_ids: list[UUID] = []
    for index, (delivery_id, amount) in enumerate(zip(deliveries, expected_amounts, strict=True)):
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
                "device_captured_at": f"2026-08-01T13:0{index}:00Z",
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
                **{"Idempotency-Key": f"cod-residual-confirm-{index}"},
            ),
            json={
                "confirmation_id": str(uuid4()),
                "expected_delivery_version": 1,
                "recipient_name": f"Residual Recipient {index}",
                "device_captured_at": f"2026-08-01T13:0{index}:00Z",
                "evidence_ids": [evidence_id],
                "lines": [{"line_id": line_id, "accepted_quantity_base": "1.000000"}],
                "collection": {
                    "payment_receipt_id": str(uuid4()),
                    "payment_method": "cash",
                    "amount": amount,
                    "currency": "PHP",
                    "received_at": f"2026-08-01T13:0{index}:00Z",
                    "external_reference": None,
                    "evidence": None,
                },
            },
        )
        assert confirmed.status_code == 201, confirmed.text
        assert confirmed.json()["collection"]["amount_due"] == amount
        event_ids.append(UUID(confirmed.json()["outbox_event_id"]))

    engine = create_async_engine(postgres_url)
    for event_id in reversed(event_ids):
        async with engine.begin() as connection:
            session = AsyncSession(bind=connection, expire_on_commit=False)
            await create_draft_invoice_for_event(session, event_id)
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT collection.amount_due, receipt.amount AS receipt_amount,
                               invoice.grand_total
                        FROM delivery_confirmations confirmation
                        JOIN cod_collections collection USING (confirmation_id)
                        JOIN payment_receipts receipt USING (payment_receipt_id)
                        JOIN draft_invoices invoice
                          ON invoice.delivery_confirmation_id = confirmation.confirmation_id
                        ORDER BY confirmation.confirmed_at, confirmation.confirmation_id
                        """
                    )
                )
            )
            .mappings()
            .all()
        )
    await engine.dispose()
    assert [dict(row) for row in rows] == [
        {
            "amount_due": Decimal(amount),
            "receipt_amount": Decimal(amount),
            "grand_total": Decimal(amount),
        }
        for amount in expected_amounts
    ]


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
    unit_cost: str = "0",
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
                  'opening_stock', 2.000000, :unit_cost,
                  2.000000 * CAST(:unit_cost AS numeric),
                  'PHP', 'TEST-SEED', 'EA',
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
                "unit_cost": unit_cost,
                "warehouse_id": fixture["warehouse_id"],
            },
        )
        await connection.execute(
            text(
                """
                UPDATE inventory_valuation
                SET inventory_value = quantity_on_hand * CAST(:unit_cost AS numeric),
                    moving_average_unit_cost = CAST(:unit_cost AS numeric)
                WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                """
            ),
            {
                "sku_id": fixture["sku_id"],
                "unit_cost": unit_cost,
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
async def test_cod_on_account_conversion_enforces_current_customer_credit_controls(
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
    engine = create_async_engine(postgres_url)

    async def attempt(key: str):
        return await confirmation_client.post(
            f"/v1/deliveries/{delivery_id}/cod-on-account-conversions",
            headers=auth(
                confirmation_settings,
                "cod-credit-approver-mnl",
                **{"Idempotency-Key": key},
            ),
            json={
                "conversion_id": str(uuid4()),
                "expected_delivery_version": 1,
                "reason": "Current credit controls must authorize this exception",
            },
        )

    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE customer_accounts SET status = 'inactive' WHERE customer_id = :id"),
            {"id": fixture["customer_id"]},
        )
    inactive = await attempt("cod-conversion-inactive-customer")
    assert inactive.status_code == 409, inactive.text
    assert inactive.json()["error"]["code"] == "customer_inactive"

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE customer_accounts SET status = 'active', credit_hold = true "
                "WHERE customer_id = :id"
            ),
            {"id": fixture["customer_id"]},
        )
    held = await attempt("cod-conversion-credit-held-customer")
    assert held.status_code == 409, held.text
    assert held.json()["error"]["code"] == "customer_credit_hold"

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE customer_accounts SET credit_hold = false, payment_terms = '' "
                "WHERE customer_id = :id"
            ),
            {"id": fixture["customer_id"]},
        )
    missing_terms = await attempt("cod-conversion-missing-payment-terms")
    assert missing_terms.status_code == 409, missing_terms.text
    assert missing_terms.json()["error"]["code"] == "payment_terms_required"
    async with engine.connect() as connection:
        counts = (
            (
                await connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM cod_on_account_conversions) AS conversions, "
                        "(SELECT count(*) FROM credit_exposure_entries "
                        " WHERE source_type = 'cod_on_account_conversion') AS exposures"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert dict(counts) == {"conversions": 0, "exposures": 0}
    await engine.dispose()


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
            "delivery_line_id": payload["lines"][0]["delivery_line_id"],
            "line_id": fixture["line_id"],
            "sku_id": fixture["sku_id"],
            "accepted_quantity_base": "2.000000",
            "refused_quantity_base": "0",
            "damaged_quantity_base": "0",
            "short_missing_quantity_base": "0",
            "still_undelivered_quantity_base": "0",
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
async def test_cash_on_delivery_confirmation_records_cleared_unapplied_cash_atomically(
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
    assigned = await confirmation_client.get(
        f"/v1/deliveries/{delivery_id}",
        headers=auth(confirmation_settings, "delivery-mnl"),
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["collection_amount_due"] == "224.00"
    quote = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmation-quote",
        headers=auth(confirmation_settings, "delivery-mnl"),
        json={
            "expected_delivery_version": 1,
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
        },
    )
    assert quote.status_code == 200, quote.text
    assert quote.json() == {
        "delivery_id": delivery_id,
        "delivery_version": 1,
        "accepted_quantity_base": "2.000000",
        "amount_due": "224.00",
        "currency": "PHP",
    }
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
    insufficient = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "cod-confirmation-short"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "expected_delivery_version": 1,
            "recipient_name": "Ana Santos",
            "device_captured_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
            "collection": {
                "payment_receipt_id": str(uuid4()),
                "payment_method": "cash",
                "amount": "223.99",
                "currency": "PHP",
                "received_at": "2026-08-01T13:00:00Z",
                "external_reference": None,
                "evidence": None,
            },
        },
    )
    assert insufficient.status_code == 409, insufficient.text
    assert insufficient.json()["error"]["code"] == "cod_collection_amount_conflict"
    overpaid_command = insufficient.request.content.decode()
    overpaid_payload = json.loads(overpaid_command)
    overpaid_payload["confirmation_id"] = str(uuid4())
    overpaid_payload["collection"]["payment_receipt_id"] = str(uuid4())
    overpaid_payload["collection"]["amount"] = "224.01"
    overpaid = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "cod-confirmation-over"},
        ),
        json=overpaid_payload,
    )
    assert overpaid.status_code == 409, overpaid.text
    assert overpaid.json()["error"]["code"] == "cod_collection_amount_conflict"
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        counts = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT (SELECT count(*) FROM delivery_confirmations) AS confirmations,
                           (SELECT count(*) FROM payment_receipts) AS receipts,
                           (SELECT count(*) FROM cod_collections) AS collections
                    """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(counts) == {"confirmations": 0, "receipts": 0, "collections": 0}
    payment_receipt_id = str(uuid4())
    confirmed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "cod-confirmation-cash"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "expected_delivery_version": 1,
            "recipient_name": "Ana Santos",
            "device_captured_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
            "collection": {
                "payment_receipt_id": payment_receipt_id,
                "payment_method": "cash",
                "amount": "224.00",
                "currency": "PHP",
                "received_at": "2026-08-01T13:00:00Z",
                "external_reference": None,
                "evidence": None,
            },
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["collection"] == {
        "payment_receipt_id": payment_receipt_id,
        "amount_due": "224.00",
        "amount_collected": "224.00",
        "currency": "PHP",
        "payment_method": "cash",
        "status": "cleared",
        "application_status": "unapplied",
        "cash_reconciliation_status": "pending",
    }
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT pr.amount, prs.state, prb.allocated_amount,
                               cri.status AS cash_status, cc.amount_due, cc.status
                        FROM cod_collections cc
                        JOIN payment_receipts pr USING (payment_receipt_id)
                        JOIN payment_receipt_status prs USING (payment_receipt_id)
                        JOIN payment_receipt_balances prb USING (payment_receipt_id)
                        JOIN cash_reconciliation_items cri USING (payment_receipt_id)
                        WHERE cc.confirmation_id = :confirmation_id
                        """
                    ),
                    {"confirmation_id": confirmed.json()["confirmation_id"]},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(row) == {
        "amount": Decimal("224.000000"),
        "state": "cleared",
        "allocated_amount": Decimal("0.000000"),
        "cash_status": "pending",
        "amount_due": Decimal("224.000000"),
        "status": "cleared",
    }
    reconciliation_id = str(uuid4())
    reconciled = await confirmation_client.post(
        f"/v1/finance/payment-receipts/{payment_receipt_id}/cash-reconciliation",
        headers=auth(
            confirmation_settings,
            "finance-recorder",
            **{"Idempotency-Key": "reconcile-cod-cash"},
        ),
        json={
            "cash_reconciliation_id": reconciliation_id,
            "counted_amount": "223.00",
            "reconciled_at": "2026-08-01T17:00:00Z",
            "reason": "PHP 1.00 documented till shortage",
        },
    )
    assert reconciled.status_code == 201, reconciled.text
    assert reconciled.json()["variance_amount"] == "-1.00"
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        reconciliation = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT cc.status, cre.event_type, cre.expected_amount,
                               cre.counted_amount, cre.variance_amount, cre.reason
                        FROM cod_collections cc
                        JOIN cash_reconciliation_events cre USING (payment_receipt_id)
                        WHERE cc.payment_receipt_id = :payment_receipt_id
                        """
                    ),
                    {"payment_receipt_id": payment_receipt_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(reconciliation) == {
        "status": "reconciled",
        "event_type": "reconciled",
        "expected_amount": Decimal("224.000000"),
        "counted_amount": Decimal("223.000000"),
        "variance_amount": Decimal("-1.000000"),
        "reason": "PHP 1.00 documented till shortage",
    }
    adjusted = await confirmation_client.post(
        f"/v1/finance/payment-receipts/{payment_receipt_id}/cash-reconciliation/adjustments",
        headers=auth(
            confirmation_settings,
            "finance-recorder",
            **{"Idempotency-Key": "adjust-cod-cash-count"},
        ),
        json={
            "cash_reconciliation_id": str(uuid4()),
            "counted_amount": "224.00",
            "reconciled_at": "2026-08-01T17:10:00Z",
            "reason": "Second checker found the missing peso in the sealed pouch",
        },
    )
    assert adjusted.status_code == 201, adjusted.text
    assert adjusted.json()["event_type"] == "adjusted"
    assert adjusted.json()["variance_amount"] == "0.00"
    reversed_reconciliation = await confirmation_client.post(
        f"/v1/finance/payment-receipts/{payment_receipt_id}/cash-reconciliation/reversal",
        headers=auth(
            confirmation_settings,
            "finance-recorder",
            **{"Idempotency-Key": "reverse-cod-reconciliation"},
        ),
        json={
            "cash_reconciliation_id": str(uuid4()),
            "reversed_at": "2026-08-01T17:20:00Z",
            "reason": "Deposit batch was assigned to the wrong cashier session",
        },
    )
    assert reversed_reconciliation.status_code == 201, reversed_reconciliation.text
    assert reversed_reconciliation.json()["event_type"] == "reversed"
    assert reversed_reconciliation.json()["status"] == "pending"
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        history = list(
            (
                await connection.execute(
                    text(
                        """
                        SELECT event_type FROM cash_reconciliation_events
                        WHERE payment_receipt_id = :payment_receipt_id
                        ORDER BY occurred_at
                        """
                    ),
                    {"payment_receipt_id": payment_receipt_id},
                )
            ).scalars()
        )
        projection = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT cri.status AS cash_status, cc.status AS cod_status
                        FROM cash_reconciliation_items cri
                        JOIN cod_collections cc USING (payment_receipt_id)
                        WHERE cri.payment_receipt_id = :payment_receipt_id
                        """
                    ),
                    {"payment_receipt_id": payment_receipt_id},
                )
            )
            .mappings()
            .one()
        )
    assert history == ["reconciled", "adjusted", "reversed"]
    assert dict(projection) == {"cash_status": "pending", "cod_status": "cleared"}
    with pytest.raises(DBAPIError, match="immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE cash_reconciliation_events SET reason = 'rewritten' "
                    "WHERE payment_receipt_id = :payment_receipt_id"
                ),
                {"payment_receipt_id": payment_receipt_id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM cash_reconciliation_events "
                    "WHERE payment_receipt_id = :payment_receipt_id"
                ),
                {"payment_receipt_id": payment_receipt_id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM cod_collections WHERE payment_receipt_id = :payment_receipt_id"),
                {"payment_receipt_id": payment_receipt_id},
            )
    with pytest.raises(DBAPIError, match="ck_cash_reconciliation_events_amounts"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO cash_reconciliation_events(
                      cash_reconciliation_event_id, payment_receipt_id,
                      cash_reconciliation_id, event_type, expected_amount,
                      counted_amount, variance_amount, reason, actor_subject,
                      occurred_at, idempotency_key
                    ) VALUES (
                      :event_id, :payment_receipt_id, :reconciliation_id,
                      'adjusted', 224, 223, 0, 'invalid arithmetic fixture',
                      'finance-recorder', now(), :idempotency_key
                    )
                    """
                ),
                {
                    "event_id": uuid4(),
                    "payment_receipt_id": payment_receipt_id,
                    "reconciliation_id": uuid4(),
                    "idempotency_key": f"invalid-reconciliation-{uuid4()}",
                },
            )
    reversed_payment = await confirmation_client.post(
        f"/v1/finance/payment-receipts/{payment_receipt_id}/reversal",
        headers=auth(
            confirmation_settings,
            "finance-reverser",
            **{"Idempotency-Key": "reverse-reconciled-cod-cash"},
        ),
        json={
            "payment_reversal_id": str(uuid4()),
            "reason": "The collected cash receipt was voided after reconciliation review",
            "reversed_at": "2026-08-01T17:30:00Z",
        },
    )
    assert reversed_payment.status_code == 201, reversed_payment.text
    reconcile_reversed = await confirmation_client.post(
        f"/v1/finance/payment-receipts/{payment_receipt_id}/cash-reconciliation",
        headers=auth(
            confirmation_settings,
            "finance-recorder",
            **{"Idempotency-Key": "reconcile-reversed-cod-cash"},
        ),
        json={
            "cash_reconciliation_id": str(uuid4()),
            "counted_amount": "224.00",
            "reconciled_at": "2026-08-01T17:35:00Z",
            "reason": "A reversed receipt must remain terminal",
        },
    )
    assert reconcile_reversed.status_code == 409, reconcile_reversed.text
    assert (
        reconcile_reversed.json()["error"]["code"] == "cash_reconciliation_payment_state_conflict"
    )
    async with engine.connect() as connection:
        cod_status = await connection.scalar(
            text("SELECT status FROM cod_collections WHERE payment_receipt_id = :id"),
            {"id": payment_receipt_id},
        )
    assert cod_status == "reversed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_non_cash_cod_requires_maker_checker_clearance_before_confirmation(
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
    receipt = await record_receipt(
        confirmation_client,
        confirmation_settings,
        fixture,
        payment_method="bank_transfer",
        external_reference="COD-BANK-1001",
        key="cod-transfer-capture",
        actor="delivery-mnl",
    )
    assert receipt.status_code == 201, receipt.text
    assert receipt.json()["status"] == "pending_verification"
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
    confirmation_id = str(uuid4())
    command = {
        "confirmation_id": confirmation_id,
        "expected_delivery_version": 1,
        "recipient_name": "Ana Santos",
        "device_captured_at": "2026-08-01T13:00:00Z",
        "evidence_ids": [evidence_id],
        "lines": [
            {
                "line_id": fixture["line_id"],
                "accepted_quantity_base": "2.000000",
            }
        ],
        "collection": {
            "payment_receipt_id": receipt.json()["payment_receipt_id"],
            "payment_method": "bank_transfer",
            "amount": "224.00",
            "currency": "PHP",
            "received_at": "2026-08-01T13:00:00Z",
            "external_reference": "COD-BANK-1001",
            "evidence": None,
        },
    }
    pending = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "cod-transfer-pending"},
        ),
        json=command,
    )
    assert pending.status_code == 409, pending.text
    assert pending.json()["error"]["code"] == "cod_payment_verification_required"
    verified = await confirmation_client.post(
        f"/v1/finance/payment-receipts/{receipt.json()['payment_receipt_id']}/verification",
        headers=auth(
            confirmation_settings,
            "finance-verifier",
            **{"Idempotency-Key": "cod-transfer-verify"},
        ),
        json={
            "decision": "cleared",
            "verified_at": "2026-08-01T13:05:00Z",
            "reason": "Bank settlement verified independently",
        },
    )
    assert verified.status_code == 201, verified.text
    confirmed = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "cod-transfer-cleared"},
        ),
        json=command,
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["collection"] == {
        "payment_receipt_id": receipt.json()["payment_receipt_id"],
        "amount_due": "224.00",
        "amount_collected": "224.00",
        "currency": "PHP",
        "payment_method": "bank_transfer",
        "status": "cleared",
        "application_status": "unapplied",
        "cash_reconciliation_status": None,
    }
    reversed_payment = await confirmation_client.post(
        f"/v1/finance/payment-receipts/{receipt.json()['payment_receipt_id']}/reversal",
        headers=auth(
            confirmation_settings,
            "finance-reverser",
            **{"Idempotency-Key": "reverse-cleared-cod-transfer"},
        ),
        json={
            "payment_reversal_id": str(uuid4()),
            "reason": "Provider recalled the transfer after delivery",
            "reversed_at": "2026-08-02T09:00:00Z",
        },
    )
    assert reversed_payment.status_code == 201, reversed_payment.text
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        cod_status = await connection.scalar(
            text(
                "SELECT status FROM cod_collections WHERE payment_receipt_id = :payment_receipt_id"
            ),
            {"payment_receipt_id": receipt.json()["payment_receipt_id"]},
        )
    await engine.dispose()
    assert cod_status == "reversed"


@pytest.mark.asyncio
async def test_distinct_authorized_user_converts_unpaid_cod_to_serialized_credit(
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
    conversion_id = str(uuid4())
    conversion_command = {
        "conversion_id": conversion_id,
        "expected_delivery_version": 1,
        "reason": "Customer accepted delivery and requested approved account terms",
    }
    self_approval = await confirmation_client.post(
        f"/v1/deliveries/{delivery_id}/cod-on-account-conversions",
        headers=auth(
            confirmation_settings,
            "delivery-mnl",
            **{"Idempotency-Key": "driver-self-conversion"},
        ),
        json=conversion_command,
    )
    assert self_approval.status_code == 403, self_approval.text
    competing_id = str(uuid4())
    approved, competing = await asyncio.gather(
        confirmation_client.post(
            f"/v1/deliveries/{delivery_id}/cod-on-account-conversions",
            headers=auth(
                confirmation_settings,
                "cod-credit-approver-mnl",
                **{"Idempotency-Key": "approve-cod-credit-a"},
            ),
            json=conversion_command,
        ),
        confirmation_client.post(
            f"/v1/deliveries/{delivery_id}/cod-on-account-conversions",
            headers=auth(
                confirmation_settings,
                "cod-credit-approver-mnl",
                **{"Idempotency-Key": "approve-cod-credit-b"},
            ),
            json={**conversion_command, "conversion_id": competing_id},
        ),
    )
    assert sorted([approved.status_code, competing.status_code]) == [201, 409]
    approved = next(response for response in (approved, competing) if response.status_code == 201)
    conversion_id = approved.json()["conversion_id"]
    assert approved.status_code == 201, approved.text
    assert approved.json() == {
        "conversion_id": conversion_id,
        "delivery_id": delivery_id,
        "amount": "224.00",
        "currency": "PHP",
        "status": "approved",
        "approved_by": "cod-credit-approver-mnl",
    }
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
            **{"Idempotency-Key": "confirm-converted-cod"},
        ),
        json={
            "confirmation_id": str(uuid4()),
            "expected_delivery_version": 1,
            "recipient_name": "Ana Santos",
            "device_captured_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
            "on_account_conversion_id": conversion_id,
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["collection"] is None
    assert confirmed.json()["on_account_conversion"] == {
        "conversion_id": conversion_id,
        "delivery_id": delivery_id,
        "amount": "224.00",
        "currency": "PHP",
        "status": "consumed",
        "approved_by": "cod-credit-approver-mnl",
    }
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT cce.approved_uninvoiced, cce.version,
                               cee.amount_delta, cee.source_type,
                               conversion.status,
                               (SELECT count(*) FROM payment_receipts) AS receipt_count
                        FROM customer_credit_exposure cce
                        JOIN credit_exposure_entries cee USING (customer_id)
                        JOIN cod_on_account_conversions conversion
                          ON conversion.conversion_id = cee.source_id
                        WHERE cce.customer_id = :customer_id
                        """
                    ),
                    {"customer_id": fixture["customer_id"]},
                )
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "approved_uninvoiced": Decimal("224.000000"),
        "version": 1,
        "amount_delta": Decimal("224.000000"),
        "source_type": "cod_on_account_conversion",
        "status": "consumed",
        "receipt_count": 0,
    }
    with pytest.raises(DBAPIError, match="Invalid COD On Account approval transition"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE cod_on_account_conversions SET status = 'approved', "
                    "confirmation_id = NULL WHERE conversion_id = :conversion_id"
                ),
                {"conversion_id": conversion_id},
            )
    with pytest.raises(DBAPIError, match="Invalid COD On Account approval transition"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE cod_on_account_conversions "
                    "SET consumed_amount = consumed_amount - 1 "
                    "WHERE conversion_id = :conversion_id"
                ),
                {"conversion_id": conversion_id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM cod_on_account_conversions WHERE conversion_id = :conversion_id"),
                {"conversion_id": conversion_id},
            )
    async with engine.connect() as connection:
        ownership_constraints = set(
            (
                await connection.execute(
                    text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE conname IN (
                          'uq_delivery_confirmation_delivery_identity',
                          'fk_cod_collection_confirmation_delivery',
                          'fk_cod_conversion_confirmation_delivery'
                        )
                        """
                    )
                )
            ).scalars()
        )
    assert ownership_constraints == {
        "uq_delivery_confirmation_delivery_identity",
        "fk_cod_collection_confirmation_delivery",
        "fk_cod_conversion_confirmation_delivery",
    }
    await engine.dispose()

    migration_config = AlembicConfig("apps/api/alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", postgres_url)
    request_logger = logging.getLogger("tradeflow_api.request")
    assert not request_logger.disabled
    await asyncio.to_thread(alembic_command.downgrade, migration_config, "0013")
    await asyncio.to_thread(alembic_command.upgrade, migration_config, "head")
    assert not request_logger.disabled
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        migrated_conversion = (
            (
                await connection.execute(
                    text(
                        "SELECT amount, consumed_amount, status "
                        "FROM cod_on_account_conversions "
                        "WHERE conversion_id = :conversion_id"
                    ),
                    {"conversion_id": conversion_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert migrated_conversion == {
        "amount": Decimal("224.000000"),
        "consumed_amount": Decimal("224.000000"),
        "status": "consumed",
    }


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
