from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_delivery_confirmation_contract import FakeObjectStorage
from test_invoice_posting_contract import confirmed_delivery_and_invoice
from test_payment_clearance_contract import auth
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def allocation_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def allocation_client(
    allocation_settings: Settings,
    fake_storage: object,
) -> AsyncIterator[AsyncClient]:
    app = create_app(allocation_settings)
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
) -> None:
    posted = await client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            settings,
            "finance-recorder",
            **{"Idempotency-Key": f"post-{draft_invoice_id}"},
        ),
        json={},
    )
    assert posted.status_code == 201, posted.text


async def _create_cleared_receipt(
    postgres_url: str,
    *,
    customer_id: str,
    branch_id: str,
    amount: str,
    currency: str = "PHP",
    payment_method_kind: str = "cash",
    intended_sales_order_id: str | None = None,
    intended_fulfillment_order_id: str | None = None,
) -> str:
    engine = create_async_engine(postgres_url)
    payment_receipt_id = str(uuid4())
    async with engine.begin() as connection:
        method_id = await connection.scalar(
            text(
                "SELECT payment_method_id FROM payment_methods "
                "WHERE kind = :kind AND is_active = true LIMIT 1"
            ),
            {"kind": payment_method_kind},
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
                ":method_kind, :method_kind, :amount, :currency, now(), 'finance-recorder', "
                ":correlation_id, :idempotency_key)"
            ),
            {
                "receipt_id": payment_receipt_id,
                "company_id": str(company_id),
                "branch_id": branch_id,
                "customer_id": customer_id,
                "method_id": str(method_id),
                "method_kind": payment_method_kind,
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
            {
                "receipt_id": payment_receipt_id,
                "amount": amount,
            },
        )
        if intended_sales_order_id is not None:
            await connection.execute(
                text(
                    "UPDATE payment_receipts SET intended_sales_order_id = :order_id "
                    "WHERE payment_receipt_id = :receipt_id"
                ),
                {
                    "receipt_id": payment_receipt_id,
                    "order_id": intended_sales_order_id,
                },
            )
        if intended_fulfillment_order_id is not None:
            await connection.execute(
                text(
                    "UPDATE payment_receipts SET intended_fulfillment_order_id = :fo_id "
                    "WHERE payment_receipt_id = :receipt_id"
                ),
                {
                    "receipt_id": payment_receipt_id,
                    "fo_id": intended_fulfillment_order_id,
                },
            )
    await engine.dispose()
    return payment_receipt_id


@pytest.mark.asyncio
async def test_manual_allocation_reduces_invoice_open_balance(
    allocation_client: AsyncClient,
    allocation_settings: Settings,
    postgres_url: str,
    fake_storage: object,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        allocation_client,
        allocation_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(allocation_client, allocation_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="224.00",
    )

    allocated = await allocation_client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=auth(
            allocation_settings,
            "finance-recorder",
            **{"Idempotency-Key": "allocate-full"},
        ),
        json={"allocations": [{"invoice_id": draft_invoice_id, "amount": "224.00"}]},
    )
    assert allocated.status_code == 201, allocated.text
    body = allocated.json()
    assert len(body) == 1
    assert body[0]["invoice_id"] == draft_invoice_id
    assert body[0]["amount"] == "224.00"

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
        balance = (
            (
                await connection.execute(
                    text(
                        "SELECT allocated_amount FROM payment_receipt_balances "
                        "WHERE payment_receipt_id = :receipt_id"
                    ),
                    {"receipt_id": receipt_id},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert exposure["open_balance"] == Decimal("0")
    assert balance["allocated_amount"] == Decimal("224.00")


@pytest.mark.asyncio
async def test_manual_allocation_is_idempotent(
    allocation_client: AsyncClient,
    allocation_settings: Settings,
    postgres_url: str,
    fake_storage: object,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        allocation_client,
        allocation_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(allocation_client, allocation_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="224.00",
    )
    headers = auth(
        allocation_settings,
        "finance-recorder",
        **{"Idempotency-Key": "allocate-idempotent"},
    )

    first = await allocation_client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=headers,
        json={"allocations": [{"invoice_id": draft_invoice_id, "amount": "100.00"}]},
    )
    assert first.status_code == 201, first.text
    replay = await allocation_client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=headers,
        json={"allocations": [{"invoice_id": draft_invoice_id, "amount": "100.00"}]},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert replay.headers["X-Idempotency-Replayed"] == "true"


@pytest.mark.asyncio
async def test_manual_allocation_rejects_over_allocation_of_receipt(
    allocation_client: AsyncClient,
    allocation_settings: Settings,
    postgres_url: str,
    fake_storage: object,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        allocation_client,
        allocation_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(allocation_client, allocation_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="100.00",
    )

    rejected = await allocation_client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=auth(
            allocation_settings,
            "finance-recorder",
            **{"Idempotency-Key": "over-allocate"},
        ),
        json={"allocations": [{"invoice_id": draft_invoice_id, "amount": "224.00"}]},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "payment_receipt_overallocated"


@pytest.mark.asyncio
async def test_manual_allocation_rejects_over_allocation_of_invoice(
    allocation_client: AsyncClient,
    allocation_settings: Settings,
    postgres_url: str,
    fake_storage: object,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        allocation_client,
        allocation_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(allocation_client, allocation_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="500.00",
    )

    rejected = await allocation_client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=auth(
            allocation_settings,
            "finance-recorder",
            **{"Idempotency-Key": "over-allocate-invoice"},
        ),
        json={"allocations": [{"invoice_id": draft_invoice_id, "amount": "300.00"}]},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "invoice_overallocated"


@pytest.mark.asyncio
async def test_manual_allocation_rejects_missing_capability(
    allocation_client: AsyncClient,
    allocation_settings: Settings,
    postgres_url: str,
    fake_storage: object,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        allocation_client,
        allocation_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(allocation_client, allocation_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="224.00",
    )

    denied = await allocation_client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=auth(
            allocation_settings,
            "sales-mnl",
            **{"Idempotency-Key": "allocate-denied"},
        ),
        json={"allocations": [{"invoice_id": draft_invoice_id, "amount": "224.00"}]},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "capability_required"


@pytest.mark.asyncio
async def test_manual_allocation_rejects_out_of_scope_branch(
    allocation_client: AsyncClient,
    allocation_settings: Settings,
    postgres_url: str,
    fake_storage: object,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        allocation_client,
        allocation_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(allocation_client, allocation_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="224.00",
    )

    denied = await allocation_client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=auth(
            allocation_settings,
            "finance-ceb",
            **{"Idempotency-Key": "allocate-scope"},
        ),
        json={"allocations": [{"invoice_id": draft_invoice_id, "amount": "224.00"}]},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "operational_scope_required"


@pytest.mark.asyncio
async def test_list_allocations_returns_applied_and_unapplied_value(
    allocation_client: AsyncClient,
    allocation_settings: Settings,
    postgres_url: str,
    fake_storage: object,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        allocation_client,
        allocation_settings,
        postgres_url,
        fake_storage,
    )
    await _post_invoice(allocation_client, allocation_settings, draft_invoice_id)
    receipt_id = await _create_cleared_receipt(
        postgres_url,
        customer_id=fixture["customer_id"],
        branch_id=fixture["branch_id"],
        amount="500.00",
    )
    allocated = await allocation_client.post(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=auth(
            allocation_settings,
            "finance-recorder",
            **{"Idempotency-Key": "allocate-partial"},
        ),
        json={"allocations": [{"invoice_id": draft_invoice_id, "amount": "100.00"}]},
    )
    assert allocated.status_code == 201, allocated.text

    listed = await allocation_client.get(
        f"/v1/finance/payment-receipts/{receipt_id}/allocations",
        headers=auth(allocation_settings, "finance-recorder"),
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["cleared_amount"] == "500.000000"
    assert body["allocated_amount"] == "100.000000"
    assert body["available_amount"] == "400.000000"
    assert len(body["allocations"]) == 1
    assert body["allocations"][0]["invoice_id"] == draft_invoice_id


@pytest.mark.asyncio
async def test_auto_allocation_on_post_applies_cod_receipt(
    allocation_client: AsyncClient,
    allocation_settings: Settings,
    postgres_url: str,
    fake_storage: object,
) -> None:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        allocation_client,
        allocation_settings,
        postgres_url,
        fake_storage,
    )
    # Link a cleared COD receipt to the delivery confirmation.
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        confirmation_id = await connection.scalar(
            text(
                "SELECT delivery_confirmation_id FROM draft_invoices "
                "WHERE draft_invoice_id = :invoice_id"
            ),
            {"invoice_id": draft_invoice_id},
        )
        delivery_id = await connection.scalar(
            text(
                "SELECT delivery_id FROM delivery_confirmations "
                "WHERE confirmation_id = :confirmation_id"
            ),
            {"confirmation_id": str(confirmation_id)},
        )
        receipt_id = await _create_cleared_receipt(
            postgres_url,
            customer_id=fixture["customer_id"],
            branch_id=fixture["branch_id"],
            amount="224.00",
        )
        await connection.execute(
            text(
                "INSERT INTO cod_collections "
                "(confirmation_id, delivery_id, payment_receipt_id, amount_due, "
                "amount_collected, currency, status, collected_by) "
                "VALUES (:confirmation_id, :delivery_id, :receipt_id, '224.00', "
                "'224.00', 'PHP', 'cleared', 'delivery-mnl')"
            ),
            {
                "confirmation_id": str(confirmation_id),
                "delivery_id": str(delivery_id),
                "receipt_id": receipt_id,
            },
        )
    await engine.dispose()

    await _post_invoice(allocation_client, allocation_settings, draft_invoice_id)

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
        balance = (
            (
                await connection.execute(
                    text(
                        "SELECT allocated_amount FROM payment_receipt_balances "
                        "WHERE payment_receipt_id = :receipt_id"
                    ),
                    {"receipt_id": receipt_id},
                )
            )
            .mappings()
            .one()
        )
        allocation_count = await connection.scalar(
            text("SELECT count(*) FROM payment_allocations WHERE payment_receipt_id = :receipt_id"),
            {"receipt_id": receipt_id},
        )
    await engine.dispose()
    assert exposure["open_balance"] == Decimal("0")
    assert balance["allocated_amount"] == Decimal("224.00")
    assert allocation_count == 1
