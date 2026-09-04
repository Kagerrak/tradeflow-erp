from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
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


@pytest.fixture
def fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def credit_note_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def credit_note_client(
    credit_note_settings: Settings,
    fake_storage: FakeObjectStorage,
) -> AsyncIterator[AsyncClient]:
    app = create_app(credit_note_settings)
    app.state.object_storage = fake_storage
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _setup_credit_note_roles_and_authority(
    postgres_url: str,
    approver_subject: str = "finance-verifier",
    approver_limit: str = "1000.00",
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        role_id = await connection.scalar(
            text("SELECT role_template_id FROM role_templates WHERE code = 'FINANCE_VERIFIER'")
        )
        assert role_id is not None
        for capability in (
            "finance:credit-note-approve",
            "finance:credit-note-read",
        ):
            await connection.execute(
                text(
                    """
                    INSERT INTO role_template_capabilities(role_template_id, capability_code)
                    VALUES (:role_id, :capability)
                    ON CONFLICT (role_template_id, capability_code) DO NOTHING
                    """
                ),
                {"role_id": role_id, "capability": capability},
            )
        branch_id = await connection.scalar(
            text("SELECT branch_id FROM branches WHERE code = 'MNL'")
        )
        assert branch_id is not None
        user_subject = await connection.scalar(
            text("SELECT subject FROM users WHERE subject = :subject"),
            {"subject": approver_subject},
        )
        assert user_subject is not None
        await connection.execute(
            text(
                """
                INSERT INTO approval_authorities(
                  approval_authority_id, user_subject, capability_code, branch_id,
                  maximum_amount, maker_checker_required
                )
                VALUES (
                  :authority_id, :subject, 'finance:credit-note-approve', :branch_id,
                  :maximum_amount, true
                )
                ON CONFLICT (user_subject, capability_code, branch_id)
                  WHERE warehouse_id IS NULL
                  DO UPDATE SET maximum_amount = EXCLUDED.maximum_amount
                """
            ),
            {
                "authority_id": str(uuid4()),
                "subject": approver_subject,
                "branch_id": branch_id,
                "maximum_amount": approver_limit,
            },
        )
    await engine.dispose()


async def _seed_credit_note_document_series(
    postgres_url: str,
    prefix: str = "CN-MNL",
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        branch_id = await connection.scalar(
            text("SELECT branch_id FROM branches WHERE code = 'MNL'")
        )
        assert branch_id is not None
        await connection.execute(
            text(
                """
                INSERT INTO document_series(
                  document_series_id, branch_id, document_type, prefix, next_number
                )
                VALUES (:id, :branch_id, 'credit_note', :prefix, 1)
                ON CONFLICT (branch_id, document_type) DO NOTHING
                """
            ),
            {"id": str(uuid4()), "branch_id": branch_id, "prefix": prefix},
        )
    await engine.dispose()


async def _posted_invoice(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> tuple[dict[str, object], str]:
    fixture, draft_invoice_id = await confirmed_delivery_and_invoice(
        client, settings, postgres_url, fake_storage
    )
    post = await client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/post",
        headers=auth(
            settings,
            "finance-recorder",
            **{"Idempotency-Key": f"credit-note-post-invoice-{draft_invoice_id}"},
        ),
        json={},
    )
    assert post.status_code == 201, post.text
    return fixture, str(draft_invoice_id)


async def _request_credit_note(
    client: AsyncClient,
    settings: Settings,
    draft_invoice_id: str,
    amount: str = "50.00",
    idempotency_key: str = "credit-note-request",
    actor: str = "finance-recorder",
) -> dict[str, object]:
    response = await client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/credit-notes",
        headers=auth(settings, actor, **{"Idempotency-Key": idempotency_key}),
        json={
            "amount": amount,
            "currency": "PHP",
            "reason": "Pricing correction.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _post_credit_note(
    client: AsyncClient,
    settings: Settings,
    credit_note_id: str,
    idempotency_key: str = "credit-note-post",
    actor: str = "finance-verifier",
) -> dict[str, object]:
    response = await client.post(
        f"/v1/finance/credit-notes/{credit_note_id}/post",
        headers=auth(settings, actor, **{"Idempotency-Key": idempotency_key}),
        json={},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_credit_note_lifecycle_reduces_open_balance_and_updates_statement(
    credit_note_client: AsyncClient,
    credit_note_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await _posted_invoice(
        credit_note_client, credit_note_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)

    requested = await _request_credit_note(
        credit_note_client,
        credit_note_settings,
        draft_invoice_id,
        amount="224.00",
        idempotency_key="credit-note-request-lifecycle",
    )
    assert requested["status"] == "pending_authorization"
    assert requested["number"] is None

    posted = await _post_credit_note(
        credit_note_client,
        credit_note_settings,
        requested["credit_note_id"],
        idempotency_key="credit-note-post-lifecycle",
    )
    assert posted["status"] == "posted"
    assert posted["number"].startswith("CN-MNL-")

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
    await engine.dispose()
    assert exposure["open_balance"] == Decimal("0")

    statement_month = date.today()
    statement = await credit_note_client.get(
        f"/v1/finance/customers/{fixture['customer_id']}/statement",
        params={
            "from_date": statement_month.replace(day=1).isoformat(),
            "to_date": statement_month.isoformat(),
        },
        headers=auth(credit_note_settings, "finance-recorder"),
    )
    assert statement.status_code == 200, statement.text
    document = next(
        doc for doc in statement.json()["documents"] if str(doc["invoice_id"]) == draft_invoice_id
    )
    assert document["state"] == "credited"
    assert Decimal(document["open_amount"]) == Decimal("0")


@pytest.mark.asyncio
async def test_credit_note_request_and_post_are_idempotent(
    credit_note_client: AsyncClient,
    credit_note_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    _, draft_invoice_id = await _posted_invoice(
        credit_note_client, credit_note_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)

    request_headers = auth(
        credit_note_settings,
        "finance-recorder",
        **{"Idempotency-Key": "credit-note-request-idempotent"},
    )
    first_request = await credit_note_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/credit-notes",
        headers=request_headers,
        json={"amount": "25.00", "currency": "PHP", "reason": "Idempotent request."},
    )
    assert first_request.status_code == 201, first_request.text
    replay_request = await credit_note_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/credit-notes",
        headers=request_headers,
        json={"amount": "25.00", "currency": "PHP", "reason": "Idempotent request."},
    )
    assert replay_request.status_code == 200, replay_request.text
    assert replay_request.json() == first_request.json()
    assert replay_request.headers["X-Idempotency-Replayed"] == "true"

    credit_note_id = first_request.json()["credit_note_id"]
    post_headers = auth(
        credit_note_settings,
        "finance-verifier",
        **{"Idempotency-Key": "credit-note-post-idempotent"},
    )
    first_post = await credit_note_client.post(
        f"/v1/finance/credit-notes/{credit_note_id}/post",
        headers=post_headers,
        json={},
    )
    assert first_post.status_code == 201, first_post.text
    replay_post = await credit_note_client.post(
        f"/v1/finance/credit-notes/{credit_note_id}/post",
        headers=post_headers,
        json={},
    )
    assert replay_post.status_code == 200, replay_post.text
    assert replay_post.json() == first_post.json()
    assert replay_post.headers["X-Idempotency-Replayed"] == "true"


@pytest.mark.asyncio
async def test_credit_note_reversal_restores_open_balance(
    credit_note_client: AsyncClient,
    credit_note_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    fixture, draft_invoice_id = await _posted_invoice(
        credit_note_client, credit_note_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)

    requested = await _request_credit_note(
        credit_note_client,
        credit_note_settings,
        draft_invoice_id,
        amount="50.00",
        idempotency_key="credit-note-request-reversal",
    )
    posted = await _post_credit_note(
        credit_note_client,
        credit_note_settings,
        requested["credit_note_id"],
        idempotency_key="credit-note-post-reversal",
    )

    reverse = await credit_note_client.post(
        f"/v1/finance/credit-notes/{posted['credit_note_id']}/reverse",
        headers=auth(
            credit_note_settings,
            "finance-verifier",
            **{"Idempotency-Key": "credit-note-reverse"},
        ),
        json={"reason": "Customer rejected concession."},
    )
    assert reverse.status_code == 201, reverse.text
    assert reverse.json()["status"] == "reversed"

    second_reverse = await credit_note_client.post(
        f"/v1/finance/credit-notes/{posted['credit_note_id']}/reverse",
        headers=auth(
            credit_note_settings,
            "finance-verifier",
            **{"Idempotency-Key": "credit-note-reverse-again"},
        ),
        json={"reason": "Customer rejected concession."},
    )
    assert second_reverse.status_code == 409, second_reverse.text

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
    await engine.dispose()
    assert exposure["open_balance"] == Decimal("224.00")


@pytest.mark.asyncio
async def test_credit_note_denial_matrix(
    credit_note_client: AsyncClient,
    credit_note_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    _, draft_invoice_id = await _posted_invoice(
        credit_note_client, credit_note_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)

    # Over-credit request.
    over = await credit_note_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/credit-notes",
        headers=auth(
            credit_note_settings,
            "finance-recorder",
            **{"Idempotency-Key": "credit-note-over"},
        ),
        json={"amount": "500.00", "currency": "PHP", "reason": "Too much."},
    )
    assert over.status_code == 409, over.text
    assert over.json()["error"]["code"] == "credit_note_exceeds_eligible_value"

    # Wrong currency.
    wrong_currency = await credit_note_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/credit-notes",
        headers=auth(
            credit_note_settings,
            "finance-recorder",
            **{"Idempotency-Key": "credit-note-currency"},
        ),
        json={"amount": "10.00", "currency": "USD", "reason": "Wrong currency."},
    )
    assert wrong_currency.status_code == 422, wrong_currency.text

    # Successful request, then self-approval denied.
    requested = await _request_credit_note(
        credit_note_client,
        credit_note_settings,
        draft_invoice_id,
        amount="50.00",
        idempotency_key="credit-note-request-denial",
    )
    self_approve = await credit_note_client.post(
        f"/v1/finance/credit-notes/{requested['credit_note_id']}/post",
        headers=auth(
            credit_note_settings,
            "finance-recorder",
            **{"Idempotency-Key": "credit-note-self-approve"},
        ),
        json={},
    )
    assert self_approve.status_code == 403, self_approve.text
    assert self_approve.json()["error"]["code"] == "credit_note_maker_checker_required"

    # Approver with insufficient limit.
    await _setup_credit_note_roles_and_authority(
        postgres_url, approver_subject="finance-verifier", approver_limit="1.00"
    )
    low_limit = await credit_note_client.post(
        f"/v1/finance/credit-notes/{requested['credit_note_id']}/post",
        headers=auth(
            credit_note_settings,
            "finance-verifier",
            **{"Idempotency-Key": "credit-note-low-limit"},
        ),
        json={},
    )
    assert low_limit.status_code == 403, low_limit.text
    assert low_limit.json()["error"]["code"] == "approval_limit_exceeded"

    # Missing capability: operations administrator without credit-note approve.
    admin_no_auth = await credit_note_client.post(
        f"/v1/finance/credit-notes/{requested['credit_note_id']}/post",
        headers=auth(
            credit_note_settings,
            "operations-administrator",
            **{"Idempotency-Key": "credit-note-admin"},
        ),
        json={},
    )
    assert admin_no_auth.status_code == 403, admin_no_auth.text


@pytest.mark.asyncio
async def test_credit_note_read_is_branch_scoped(
    credit_note_client: AsyncClient,
    credit_note_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    _, draft_invoice_id = await _posted_invoice(
        credit_note_client, credit_note_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)

    requested = await _request_credit_note(
        credit_note_client,
        credit_note_settings,
        draft_invoice_id,
        amount="50.00",
        idempotency_key="credit-note-read-scope",
    )

    mnl_read = await credit_note_client.get(
        f"/v1/finance/credit-notes/{requested['credit_note_id']}",
        headers=auth(credit_note_settings, "finance-verifier"),
    )
    assert mnl_read.status_code == 200, mnl_read.text

    ceb_read = await credit_note_client.get(
        f"/v1/finance/credit-notes/{requested['credit_note_id']}",
        headers=auth(credit_note_settings, "finance-ceb"),
    )
    assert ceb_read.status_code == 403, ceb_read.text

    list_mnl = await credit_note_client.get(
        "/v1/finance/credit-notes",
        headers=auth(credit_note_settings, "finance-verifier"),
    )
    assert list_mnl.status_code == 200, list_mnl.text
    assert list_mnl.json()["total"] == 1

    list_ceb = await credit_note_client.get(
        "/v1/finance/credit-notes",
        headers=auth(credit_note_settings, "finance-ceb"),
    )
    assert list_ceb.status_code == 200, list_ceb.text
    assert list_ceb.json()["total"] == 0
