from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_delivery_confirmation_contract import FakeObjectStorage
from test_invoice_posting_contract import (
    confirmed_delivery_and_invoice,
)
from test_payment_clearance_contract import auth
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


def _utc_today() -> date:
    return datetime.now(UTC).date()


@pytest.fixture
def fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def statement_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def statement_client(
    statement_settings: Settings,
    fake_storage: FakeObjectStorage,
) -> AsyncIterator[AsyncClient]:
    app = create_app(statement_settings)
    app.state.object_storage = fake_storage
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _post_invoice(
    client: AsyncClient,
    settings: Settings,
    draft_invoice_id: str,
    idempotency_key: str = "post-invoice",
    posted_at: datetime | None = None,
) -> None:
    payload: dict[str, str | None] = {}
    if posted_at is not None:
        payload["posted_at"] = posted_at.isoformat()
    posted = await client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            settings,
            "finance-recorder",
            **{"Idempotency-Key": idempotency_key},
        ),
        json=payload,
    )
    assert posted.status_code == 201, posted.text


async def _create_cleared_receipt(
    postgres_url: str,
    *,
    customer_id: str,
    branch_id: str,
    amount: str,
    currency: str = "PHP",
) -> str:
    engine = create_async_engine(postgres_url)
    payment_receipt_id = str(uuid4())
    async with engine.begin() as connection:
        method_id = await connection.scalar(
            text(
                "SELECT payment_method_id FROM payment_methods "
                "WHERE kind = 'cash' AND is_active = true LIMIT 1"
            )
        )
        assert method_id is not None
        company_id = await connection.scalar(text("SELECT company_id FROM companies LIMIT 1"))
        assert company_id is not None
        await connection.execute(
            text(
                "INSERT INTO payment_receipts "
                "(payment_receipt_id, company_id, branch_id, customer_id, payment_method_id, "
                "payment_method_code, payment_method_kind, amount, currency, received_at, "
                "recorded_by, correlation_id, idempotency_key) "
                "VALUES (:receipt_id, :company_id, :branch_id, :customer_id, :method_id, "
                "'cash', 'cash', :amount, :currency, now(), 'finance-recorder', "
                ":correlation_id, :idempotency_key)"
            ),
            {
                "receipt_id": payment_receipt_id,
                "company_id": str(company_id),
                "branch_id": branch_id,
                "customer_id": customer_id,
                "method_id": str(method_id),
                "amount": amount,
                "currency": currency,
                "correlation_id": str(uuid4()),
                "idempotency_key": str(uuid4()),
            },
        )
        await connection.execute(
            text(
                "INSERT INTO payment_receipt_status "
                "(payment_receipt_id, company_id, payment_method_id, state, version) "
                "VALUES (:receipt_id, :company_id, :method_id, 'cleared', 1)"
            ),
            {
                "receipt_id": payment_receipt_id,
                "company_id": str(company_id),
                "method_id": str(method_id),
            },
        )
        await connection.execute(
            text(
                "INSERT INTO payment_receipt_balances "
                "(payment_receipt_id, cleared_amount, allocated_amount, "
                "coverage_designated_amount, version) "
                "VALUES (:receipt_id, :amount, 0, 0, 1)"
            ),
            {"receipt_id": payment_receipt_id, "amount": amount},
        )
    await engine.dispose()
    return payment_receipt_id


async def _allocate(
    client: AsyncClient,
    settings: Settings,
    receipt_id: str,
    invoice_id: str,
    amount: str,
    idempotency_key: str,
) -> None:
    response = await client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=auth(
            settings,
            "finance-recorder",
            **{"Idempotency-Key": idempotency_key},
        ),
        json={"allocations": [{"invoice_id": invoice_id, "amount": amount}]},
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_statement_empty_range_returns_zero_balances(
    statement_client: AsyncClient,
    statement_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, _draft_invoice_id = await confirmed_delivery_and_invoice(
        statement_client,
        statement_settings,
        postgres_url,
        fake_storage,
    )
    today = _utc_today().isoformat()
    response = await statement_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        headers=auth(statement_settings, "finance-recorder"),
        params={"from_date": today, "to_date": today},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["customer_id"] == fixture["customer_id"]
    assert body["currency"] == "PHP"
    assert Decimal(body["opening_balance"]) == Decimal("0")
    assert Decimal(body["closing_balance"]) == Decimal("0")
    assert body["lines"] == []
    assert body["documents"] == []


@pytest.mark.asyncio
async def test_statement_shows_posted_invoice_and_open_document(
    statement_client: AsyncClient,
    statement_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        statement_client,
        statement_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(statement_client, statement_settings, draft_invoice_id)

    today = _utc_today().isoformat()
    response = await statement_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        headers=auth(statement_settings, "finance-recorder"),
        params={"from_date": today, "to_date": today, "as_of": today},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["lines"]) == 1
    assert body["lines"][0]["entry_type"] == "invoice"
    assert Decimal(body["lines"][0]["amount"]) == Decimal("224")
    assert Decimal(body["closing_balance"]) == Decimal("224")

    assert len(body["documents"]) == 1
    document = body["documents"][0]
    assert document["invoice_id"] == draft_invoice_id
    assert Decimal(document["original_amount"]) == Decimal("224")
    assert Decimal(document["paid_amount"]) == Decimal("0")
    assert Decimal(document["open_amount"]) == Decimal("224")
    assert document["state"] == "unpaid"
    assert document["aging_bucket"] == "current"


@pytest.mark.asyncio
async def test_statement_reflects_partial_allocation(
    statement_client: AsyncClient,
    statement_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        statement_client,
        statement_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(statement_client, statement_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="100.00",
    )
    await _allocate(
        statement_client,
        statement_settings,
        receipt_id,
        draft_invoice_id,
        "100.00",
        "allocate-partial",
    )

    today = _utc_today().isoformat()
    response = await statement_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        headers=auth(statement_settings, "finance-recorder"),
        params={"from_date": today, "to_date": today, "as_of": today},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["lines"]) == 2
    assert Decimal(body["closing_balance"]) == Decimal("124")

    document = body["documents"][0]
    assert document["state"] == "partially_paid"
    assert Decimal(document["paid_amount"]) == Decimal("100")
    assert Decimal(document["open_amount"]) == Decimal("124")


@pytest.mark.asyncio
async def test_statement_reflects_full_payment(
    statement_client: AsyncClient,
    statement_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        statement_client,
        statement_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(statement_client, statement_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="224.00",
    )
    await _allocate(
        statement_client,
        statement_settings,
        receipt_id,
        draft_invoice_id,
        "224.00",
        "allocate-full",
    )

    today = _utc_today().isoformat()
    response = await statement_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        headers=auth(statement_settings, "finance-recorder"),
        params={"from_date": today, "to_date": today, "as_of": today},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["closing_balance"]) == Decimal("0")
    document = body["documents"][0]
    assert document["state"] == "paid"
    assert Decimal(document["open_amount"]) == Decimal("0")


@pytest.mark.asyncio
async def test_statement_aging_buckets_overdue_invoice(
    statement_client: AsyncClient,
    statement_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        statement_client,
        statement_settings,
        postgres_url,
        fake_storage,
    )
    posted_at = datetime.fromisoformat((_utc_today() - timedelta(days=45)).isoformat())
    await _post_invoice(
        statement_client,
        statement_settings,
        draft_invoice_id,
        posted_at=posted_at,
    )

    as_of = _utc_today().isoformat()
    from_date = posted_at.date().isoformat()
    response = await statement_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        headers=auth(statement_settings, "finance-recorder"),
        params={"from_date": from_date, "to_date": as_of, "as_of": as_of},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    document = body["documents"][0]
    assert document["state"] == "overdue"
    assert document["aging_bucket"] == "31-60"


@pytest.mark.asyncio
async def test_statement_branch_scope_hides_other_branch(
    statement_client: AsyncClient,
    statement_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        statement_client,
        statement_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(statement_client, statement_settings, draft_invoice_id)

    today = _utc_today().isoformat()
    response = await statement_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        headers=auth(statement_settings, "finance-ceb"),
        params={"from_date": today, "to_date": today, "as_of": today},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lines"] == []
    assert body["documents"] == []
    assert Decimal(body["closing_balance"]) == Decimal("0")


@pytest.mark.asyncio
async def test_statement_rejects_missing_capability(
    statement_client: AsyncClient,
    statement_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, _draft_invoice_id = await confirmed_delivery_and_invoice(
        statement_client,
        statement_settings,
        postgres_url,
        fake_storage,
    )
    today = _utc_today().isoformat()
    response = await statement_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        headers=auth(statement_settings, "sales-mnl"),
        params={"from_date": today, "to_date": today},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "capability_required"


@pytest.mark.asyncio
async def test_statement_rebuild_reconciles_credit_exposure(
    statement_client: AsyncClient,
    statement_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        statement_client,
        statement_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(statement_client, statement_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="100.00",
    )
    await _allocate(
        statement_client,
        statement_settings,
        receipt_id,
        draft_invoice_id,
        "100.00",
        "allocate-partial",
    )

    today = _utc_today().isoformat()
    response = await statement_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        headers=auth(statement_settings, "finance-recorder"),
        params={"from_date": today, "to_date": today, "as_of": today},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        rebuilt = (
            (
                await connection.execute(
                    text(
                        "SELECT coalesce(sum(amount), 0) AS open_balance "
                        "FROM customer_ledger_entries "
                        "WHERE customer_id = :customer_id AND branch_id = :branch_id"
                    ),
                    {
                        "customer_id": fixture["customer_id"],
                        "branch_id": fixture["branch_id"],
                    },
                )
            )
            .mappings()
            .one()
        )
        live = (
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
    await engine.dispose()
    assert Decimal(body["closing_balance"]) == Decimal("124.00")
    assert live["open_balance"] == Decimal("124.00")
    assert rebuilt["open_balance"] == Decimal("124.00")
