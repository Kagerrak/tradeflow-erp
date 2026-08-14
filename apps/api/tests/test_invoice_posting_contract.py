from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_delivery_confirmation_contract import (
    FakeObjectStorage,
    UploadedPart,
    dispatched_prepaid_delivery,
)
from test_payment_clearance_contract import (
    auth,
)
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings
from tradeflow_api.delivery_confirmation_outbox import create_draft_invoice_for_event
from tradeflow_worker.worker import poll_delivery_confirmation_outbox

FINANCE_CAPABILITIES = [
    "finance:payment-read",
    "finance:payment-record",
    "finance:invoice-post",
    "finance:invoice-read",
    "finance:invoice-void",
    "finance:credit-note-request",
    "finance:credit-note-approve",
    "finance:credit-note-read",
    "finance:payment-allocate",
    "finance:statement-read",
    "finance:cash-reconcile",
]


@pytest.fixture
def fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def invoice_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def invoice_client(
    invoice_settings: Settings,
    fake_storage: FakeObjectStorage,
) -> AsyncIterator[AsyncClient]:
    app = create_app(invoice_settings)
    app.state.object_storage = fake_storage
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _upload_evidence(
    client: AsyncClient,
    settings: Settings,
    delivery_id: str,
    fake_storage: FakeObjectStorage,
    *,
    kind: str,
) -> str:
    fake_storage.head_content_type = "image/png"
    fake_storage.head_size_bytes = 12
    fake_storage.head_sha256 = "a" * 64
    fake_storage.computed_digest = "a" * 64
    fake_storage.multipart_parts = [UploadedPart(etag='"part"', number=1, size_bytes=12)]
    evidence_id = str(uuid4())
    intent = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/uploads",
        headers=auth(settings, "delivery-mnl"),
        json={
            "evidence_id": evidence_id,
            "kind": kind,
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T12:58:30Z",
        },
    )
    assert intent.status_code == 201, intent.text
    completed = await client.post(
        f"/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
        headers=auth(settings, "delivery-mnl"),
    )
    assert completed.status_code == 200, completed.text
    return evidence_id


async def _grant_finance_capabilities(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        for capability in FINANCE_CAPABILITIES:
            await connection.execute(
                text("INSERT INTO capabilities(code) VALUES (:code) ON CONFLICT (code) DO NOTHING"),
                {"code": capability},
            )
        role_id = await connection.scalar(
            text("SELECT role_template_id FROM role_templates WHERE code = 'FINANCE_RECORDER'")
        )
        assert role_id is not None
        for capability in FINANCE_CAPABILITIES:
            await connection.execute(
                text(
                    "INSERT INTO role_template_capabilities(role_template_id, capability_code) "
                    "VALUES (:role_id, :code) ON CONFLICT (role_template_id, capability_code) "
                    "DO NOTHING"
                ),
                {"role_id": role_id, "code": capability},
            )
    await engine.dispose()


async def confirmed_delivery_and_invoice(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> tuple[dict[str, object], str]:
    fixture, delivery_id = await dispatched_prepaid_delivery(
        client,
        settings,
        postgres_url,
    )
    await _grant_finance_capabilities(postgres_url)
    photo_id = await _upload_evidence(client, settings, delivery_id, fake_storage, kind="photo")
    signature_id = await _upload_evidence(
        client, settings, delivery_id, fake_storage, kind="signature"
    )
    confirmation_id = str(uuid4())
    confirmed = await client.post(
        f"/v1/deliveries/{delivery_id}/confirmations",
        headers=auth(
            settings,
            "delivery-mnl",
            **{"Idempotency-Key": "invoice-posting-confirm"},
        ),
        json={
            "confirmation_id": confirmation_id,
            "expected_delivery_version": 1,
            "recipient_name": "Ana Santos",
            "device_captured_at": "2026-08-01T13:00:00Z",
            "notes": "Accepted for invoice posting test.",
            "evidence_ids": [photo_id, signature_id],
            "lines": [
                {
                    "line_id": fixture["line_id"],
                    "accepted_quantity_base": "2.000000",
                }
            ],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    outbox_event_id = UUID(confirmed.json()["outbox_event_id"])

    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    )
    async with engine.begin() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        draft_invoice_id = await create_draft_invoice_for_event(session, outbox_event_id)
    await engine.dispose()
    return fixture, str(draft_invoice_id)


@pytest.mark.asyncio
async def test_post_invoice_moves_draft_to_ledger_and_updates_exposure(
    invoice_client: AsyncClient,
    invoice_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        invoice_client,
        invoice_settings,
        postgres_url,
        fake_storage,
    )
    posted = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            invoice_settings,
            "finance-recorder",
            **{"Idempotency-Key": "post-invoice"},
        ),
        json={},
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert body["draft_invoice_id"] == draft_invoice_id
    assert body["status"] == "posted"
    assert body["ledger_entry_id"]

    fetched = await invoice_client.get(
        f"/v1/finance/invoices/{draft_invoice_id}",
        headers=auth(invoice_settings, "finance-recorder"),
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "posted"
    assert fetched.json()["grand_total"] == "224.000000"

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        exposure = (
            (
                await connection.execute(
                    text(
                        "SELECT open_balance, approved_uninvoiced "
                        "FROM customer_credit_exposure WHERE customer_id = :customer_id"
                    ),
                    {"customer_id": fixture["customer_id"]},
                )
            )
            .mappings()
            .one()
        )
        ledger = (
            (
                await connection.execute(
                    text(
                        "SELECT entry_type, amount FROM customer_ledger_entries "
                        "WHERE source_id = :invoice_id"
                    ),
                    {"invoice_id": draft_invoice_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert exposure["open_balance"] == Decimal("224.00")
    assert exposure["approved_uninvoiced"] == Decimal("0")
    assert ledger["entry_type"] == "invoice"
    assert ledger["amount"] == Decimal("224.00")


@pytest.mark.asyncio
async def test_post_invoice_is_idempotent(
    invoice_client: AsyncClient,
    invoice_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        invoice_client,
        invoice_settings,
        postgres_url,
        fake_storage,
    )
    headers = auth(
        invoice_settings,
        "finance-recorder",
        **{"Idempotency-Key": "post-invoice-idempotent"},
    )
    first = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=headers,
        json={},
    )
    assert first.status_code == 201, first.text
    replay = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=headers,
        json={},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert replay.headers["X-Idempotency-Replayed"] == "true"


@pytest.mark.asyncio
async def test_post_invoice_rejects_missing_capability(
    invoice_client: AsyncClient,
    invoice_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        invoice_client,
        invoice_settings,
        postgres_url,
        fake_storage,
    )
    denied = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            invoice_settings,
            "sales-mnl",
            **{"Idempotency-Key": "post-invoice-denied"},
        ),
        json={},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "capability_required"


@pytest.mark.asyncio
async def test_post_invoice_rejects_out_of_scope_branch(
    invoice_client: AsyncClient,
    invoice_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        invoice_client,
        invoice_settings,
        postgres_url,
        fake_storage,
    )
    denied = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            invoice_settings,
            "finance-ceb",
            **{"Idempotency-Key": "post-invoice-scope"},
        ),
        json={},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "operational_scope_required"


@pytest.mark.asyncio
async def test_post_invoice_rejects_repost_with_different_key(
    invoice_client: AsyncClient,
    invoice_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        invoice_client,
        invoice_settings,
        postgres_url,
        fake_storage,
    )
    first = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            invoice_settings,
            "finance-recorder",
            **{"Idempotency-Key": "post-invoice-first"},
        ),
        json={},
    )
    assert first.status_code == 201, first.text
    second = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            invoice_settings,
            "finance-recorder",
            **{"Idempotency-Key": "post-invoice-second"},
        ),
        json={},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "invoice_not_postable"


@pytest.mark.asyncio
async def test_void_invoice_reverses_ledger_and_open_balance(
    invoice_client: AsyncClient,
    invoice_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        invoice_client,
        invoice_settings,
        postgres_url,
        fake_storage,
    )
    post = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            invoice_settings,
            "finance-recorder",
            **{"Idempotency-Key": "void-post-invoice"},
        ),
        json={},
    )
    assert post.status_code == 201, post.text

    voided = await invoice_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/void",
        headers=auth(
            invoice_settings,
            "finance-recorder",
            **{"Idempotency-Key": "void-invoice"},
        ),
        json={"reason": "Customer refused after posting."},
    )
    assert voided.status_code == 201, voided.text
    assert voided.json()["status"] == "voided"

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        exposure = (
            (
                await connection.execute(
                    text(
                        "SELECT open_balance FROM customer_credit_exposure "
                        "WHERE customer_id = :customer_id"
                    ),
                    {"customer_id": fixture["customer_id"]},
                )
            )
            .mappings()
            .one()
        )
        entries = (
            (
                await connection.execute(
                    text(
                        "SELECT entry_type, amount FROM customer_ledger_entries "
                        "WHERE invoice_id = :invoice_id ORDER BY created_at"
                    ),
                    {"invoice_id": draft_invoice_id},
                )
            )
            .mappings()
            .all()
        )
    await engine.dispose()
    assert exposure["open_balance"] == Decimal("0")
    assert [(e["entry_type"], e["amount"]) for e in entries] == [
        ("invoice", Decimal("224.00")),
        ("void", Decimal("-224.00")),
    ]


@pytest.mark.asyncio
async def test_list_invoices_is_branch_scoped(
    invoice_client: AsyncClient,
    invoice_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        invoice_client,
        invoice_settings,
        postgres_url,
        fake_storage,
    )
    mnl_list = await invoice_client.get(
        "/v1/finance/invoices",
        headers=auth(invoice_settings, "finance-recorder"),
    )
    assert mnl_list.status_code == 200, mnl_list.text
    assert mnl_list.json()["total"] == 1

    ceb_list = await invoice_client.get(
        "/v1/finance/invoices",
        headers=auth(invoice_settings, "finance-ceb"),
    )
    assert ceb_list.status_code == 200, ceb_list.text
    assert ceb_list.json()["total"] == 0
