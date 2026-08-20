from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_credit_note_contract import (
    _post_credit_note,
    _request_credit_note,
    _seed_credit_note_document_series,
    _setup_credit_note_roles_and_authority,
)
from test_customer_statement_contract import (
    _allocate,
    _create_cleared_receipt,
    _post_invoice,
)
from test_delivery_confirmation_contract import FakeObjectStorage
from test_invoice_posting_contract import confirmed_delivery_and_invoice
from test_payment_clearance_contract import auth, bootstrap_payment_clearance, create_customer
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def timeline_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def timeline_client(
    timeline_settings: Settings,
    fake_storage: FakeObjectStorage,
) -> AsyncIterator[AsyncClient]:
    app = create_app(timeline_settings)
    app.state.object_storage = fake_storage
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _bootstrap_finance_capabilities(postgres_url: str) -> None:
    """Ensure FINANCE_RECORDER role has the statement-read capability."""
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO capabilities(code) VALUES ('finance:statement-read') "
                "ON CONFLICT (code) DO NOTHING"
            )
        )
        role_id = await connection.scalar(
            text("SELECT role_template_id FROM role_templates WHERE code = 'FINANCE_RECORDER'")
        )
        if role_id is not None:
            await connection.execute(
                text(
                    "INSERT INTO role_template_capabilities(role_template_id, capability_code) "
                    "VALUES (:role_id, 'finance:statement-read') "
                    "ON CONFLICT (role_template_id, capability_code) DO NOTHING"
                ),
                {"role_id": role_id},
            )
    await engine.dispose()


async def _full_timeline_fixture(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> tuple[dict[str, object], str]:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        client,
        settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(client, settings, draft_invoice_id)

    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="100.00",
    )
    await _allocate(
        client,
        settings,
        receipt_id,
        draft_invoice_id,
        "100.00",
        "timeline-allocate",
    )

    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)
    requested = await _request_credit_note(
        client,
        settings,
        draft_invoice_id,
        amount="50.00",
        idempotency_key="timeline-credit-request",
    )
    await _post_credit_note(
        client,
        settings,
        requested["credit_note_id"],
        idempotency_key="timeline-credit-post",
    )

    return fixture, str(draft_invoice_id)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


@pytest.mark.asyncio
async def test_timeline_empty_for_customer_with_no_activity(
    timeline_client: AsyncClient,
    timeline_settings: Settings,
    postgres_url: str,
) -> None:
    organization = await bootstrap_payment_clearance(
        timeline_client,
        timeline_settings,
    )
    branch_id = organization["branches"][0]["branch_id"]
    customer = await create_customer(
        timeline_client,
        timeline_settings,
        branch_id=branch_id,
    )
    await _bootstrap_finance_capabilities(postgres_url)
    response = await timeline_client.get(
        f"/v1/finance/customers/{customer['customer_id']}/timeline",
        headers=auth(timeline_settings, "finance-recorder"),
        params={"from_date": _today(), "to_date": _today()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["customer_id"] == customer["customer_id"]
    assert body["items"] == []
    assert body["total"] == 0
    assert Decimal(body["opening_balance"]) == Decimal("0")
    assert Decimal(body["closing_balance"]) == Decimal("0")


@pytest.mark.asyncio
async def test_timeline_shows_all_event_types_and_balances(
    timeline_client: AsyncClient,
    timeline_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await _full_timeline_fixture(
        timeline_client,
        timeline_settings,
        postgres_url,
        fake_storage,
    )

    response = await timeline_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/timeline",
        headers=auth(timeline_settings, "finance-recorder"),
        params={"from_date": _today(), "to_date": _today()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    types = [event["event_type"] for event in body["items"]]
    assert "order" in types
    assert "delivery" in types
    assert "invoice" in types
    assert "payment" in types
    assert "credit" in types

    invoice_event = next(event for event in body["items"] if event["event_type"] == "invoice")
    assert Decimal(invoice_event["amount"]) == Decimal("224")
    payment_event = next(event for event in body["items"] if event["event_type"] == "payment")
    assert Decimal(payment_event["amount"]) == Decimal("-100")
    credit_event = next(event for event in body["items"] if event["event_type"] == "credit")
    assert Decimal(credit_event["amount"]) == Decimal("-50")
    order_event = next(event for event in body["items"] if event["event_type"] == "order")
    assert Decimal(order_event["amount"]) == Decimal("0")
    assert Decimal(order_event["document_value"]) == Decimal("224")

    assert Decimal(body["closing_balance"]) == Decimal("74")

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        ledger = (
            (
                await connection.execute(
                    text(
                        "SELECT coalesce(sum(amount), 0) AS balance "
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
    await engine.dispose()
    assert Decimal(body["closing_balance"]) == Decimal(str(ledger["balance"]))


@pytest.mark.asyncio
async def test_timeline_branch_scope_hides_other_branch(
    timeline_client: AsyncClient,
    timeline_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, _draft_invoice_id = await _full_timeline_fixture(
        timeline_client,
        timeline_settings,
        postgres_url,
        fake_storage,
    )

    response = await timeline_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/timeline",
        headers=auth(timeline_settings, "finance-ceb"),
        params={"from_date": _today(), "to_date": _today()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert Decimal(body["closing_balance"]) == Decimal("0")


@pytest.mark.asyncio
async def test_timeline_rejects_missing_capability(
    timeline_client: AsyncClient,
    timeline_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, _draft_invoice_id = await _full_timeline_fixture(
        timeline_client,
        timeline_settings,
        postgres_url,
        fake_storage,
    )

    response = await timeline_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/timeline",
        headers=auth(timeline_settings, "sales-mnl"),
        params={"from_date": _today(), "to_date": _today()},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "capability_required"


@pytest.mark.asyncio
async def test_timeline_salesperson_filter_narrows_results(
    timeline_client: AsyncClient,
    timeline_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, _draft_invoice_id = await _full_timeline_fixture(
        timeline_client,
        timeline_settings,
        postgres_url,
        fake_storage,
    )

    response = await timeline_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/timeline",
        headers=auth(timeline_settings, "finance-recorder"),
        params={
            "from_date": _today(),
            "to_date": _today(),
            "salesperson_id": "sales-mnl",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "order"


@pytest.mark.asyncio
async def test_timeline_pagination_returns_stable_slices(
    timeline_client: AsyncClient,
    timeline_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, _draft_invoice_id = await _full_timeline_fixture(
        timeline_client,
        timeline_settings,
        postgres_url,
        fake_storage,
    )

    first = await timeline_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/timeline",
        headers=auth(timeline_settings, "finance-recorder"),
        params={"from_date": _today(), "to_date": _today(), "limit": 1, "offset": 0},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["total"] >= 4
    assert len(first_body["items"]) == 1

    second = await timeline_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/timeline",
        headers=auth(timeline_settings, "finance-recorder"),
        params={"from_date": _today(), "to_date": _today(), "limit": 1, "offset": 1},
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["items"]) == 1
    assert first_body["items"][0]["event_id"] != second_body["items"][0]["event_id"]


@pytest.mark.asyncio
async def test_timeline_rejects_invalid_event_type(
    timeline_client: AsyncClient,
    timeline_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, _draft_invoice_id = await _full_timeline_fixture(
        timeline_client,
        timeline_settings,
        postgres_url,
        fake_storage,
    )

    response = await timeline_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/timeline",
        headers=auth(timeline_settings, "finance-recorder"),
        params={
            "from_date": _today(),
            "to_date": _today(),
            "event_type": "invalid",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_event_type"


@pytest.mark.asyncio
async def test_timeline_event_type_filter_includes_only_requested_type(
    timeline_client: AsyncClient,
    timeline_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, _draft_invoice_id = await _full_timeline_fixture(
        timeline_client,
        timeline_settings,
        postgres_url,
        fake_storage,
    )

    response = await timeline_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/timeline",
        headers=auth(timeline_settings, "finance-recorder"),
        params={
            "from_date": _today(),
            "to_date": _today(),
            "event_type": "payment",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "payment"
