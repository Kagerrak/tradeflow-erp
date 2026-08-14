from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_credit_note_contract import (
    _post_credit_note,
    _posted_invoice,
    _request_credit_note,
    _seed_credit_note_document_series,
    _setup_credit_note_roles_and_authority,
)
from test_delivery_confirmation_contract import FakeObjectStorage
from test_payment_clearance_contract import auth
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def invariant_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def invariant_client(
    invariant_settings: Settings,
    fake_storage: FakeObjectStorage,
) -> AsyncIterator[AsyncClient]:
    app = create_app(invariant_settings)
    app.state.object_storage = fake_storage
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


@pytest.fixture
async def posted_credit_note(
    invariant_client: AsyncClient,
    invariant_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> dict[str, object]:
    _, draft_invoice_id = await _posted_invoice(
        invariant_client, invariant_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)
    requested = await _request_credit_note(
        invariant_client,
        invariant_settings,
        draft_invoice_id,
        amount="50.00",
        idempotency_key="invariant-request",
    )
    posted = await _post_credit_note(
        invariant_client,
        invariant_settings,
        requested["credit_note_id"],
        idempotency_key="invariant-post",
    )
    return posted


@pytest.mark.asyncio
async def test_credit_note_table_rejects_direct_update_and_delete(
    posted_credit_note: dict[str, object],
    postgres_url: str,
) -> None:
    credit_note_id = posted_credit_note["credit_note_id"]
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        with pytest.raises(Exception):  # noqa: B017
            await connection.execute(
                text("UPDATE credit_notes SET amount = amount + 1 WHERE credit_note_id = :id"),
                {"id": credit_note_id},
            )
        with pytest.raises(Exception):  # noqa: B017
            await connection.execute(
                text("DELETE FROM credit_notes WHERE credit_note_id = :id"),
                {"id": credit_note_id},
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_credit_note_authorization_table_rejects_direct_mutation(
    posted_credit_note: dict[str, object],
    postgres_url: str,
) -> None:
    credit_note_id = posted_credit_note["credit_note_id"]
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        with pytest.raises(Exception):  # noqa: B017
            await connection.execute(
                text(
                    "UPDATE credit_note_authorizations SET authorized_by = 'x' "
                    "WHERE credit_note_id = :id"
                ),
                {"id": credit_note_id},
            )
        with pytest.raises(Exception):  # noqa: B017
            await connection.execute(
                text("DELETE FROM credit_note_authorizations WHERE credit_note_id = :id"),
                {"id": credit_note_id},
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_credit_note_posted_shape_constraint(
    invariant_client: AsyncClient,
    invariant_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    _, draft_invoice_id = await _posted_invoice(
        invariant_client, invariant_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)
    requested = await _request_credit_note(
        invariant_client,
        invariant_settings,
        draft_invoice_id,
        amount="10.00",
        idempotency_key="shape-request",
    )
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        with pytest.raises(Exception):  # noqa: B017
            await connection.execute(
                text(
                    """
                    UPDATE credit_notes
                      SET status = 'posted', number = 'CN-MNL-00000001'
                      WHERE credit_note_id = :id
                    """
                ),
                {"id": requested["credit_note_id"]},
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_document_series_numbers_are_consecutive(
    invariant_client: AsyncClient,
    invariant_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    _, draft_invoice_id = await _posted_invoice(
        invariant_client, invariant_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)

    numbers: list[str] = []
    for idx in range(3):
        requested = await _request_credit_note(
            invariant_client,
            invariant_settings,
            draft_invoice_id,
            amount="10.00",
            idempotency_key=f"gap-request-{idx}",
        )
        posted = await _post_credit_note(
            invariant_client,
            invariant_settings,
            requested["credit_note_id"],
            idempotency_key=f"gap-post-{idx}",
        )
        numbers.append(str(posted["number"]))

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        audit = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT a.series_number
                    FROM document_series_number_audit a
                    JOIN document_series s ON s.document_series_id = a.document_series_id
                    WHERE s.document_type = 'credit_note'
                    ORDER BY a.series_number
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        next_number = await connection.scalar(
            text("SELECT next_number FROM document_series WHERE document_type = 'credit_note'")
        )
    await engine.dispose()

    assert [a["series_number"] for a in audit] == [1, 2, 3]
    assert next_number == 4
    assert numbers == ["CN-MNL-00000001", "CN-MNL-00000002", "CN-MNL-00000003"]


@pytest.mark.asyncio
async def test_reversal_preserves_original_and_creates_restoring_entry(
    invariant_client: AsyncClient,
    invariant_settings: Settings,
    posted_credit_note: dict[str, object],
    postgres_url: str,
) -> None:
    credit_note_id = posted_credit_note["credit_note_id"]
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        original = (
            (
                await connection.execute(
                    text("SELECT * FROM credit_notes WHERE credit_note_id = :id"),
                    {"id": credit_note_id},
                )
            )
            .mappings()
            .one()
        )

    reverse = await invariant_client.post(
        f"/v1/finance/credit-notes/{credit_note_id}/reverse",
        headers=auth(
            invariant_settings,
            "finance-verifier",
            **{"Idempotency-Key": "invariant-reverse"},
        ),
        json={"reason": "Test reversal."},
    )
    assert reverse.status_code == 201, reverse.text

    async with engine.connect() as connection:
        updated = (
            (
                await connection.execute(
                    text("SELECT * FROM credit_notes WHERE credit_note_id = :id"),
                    {"id": credit_note_id},
                )
            )
            .mappings()
            .one()
        )
        entries = (
            (
                await connection.execute(
                    text(
                        "SELECT entry_type, source_type, amount FROM customer_ledger_entries "
                        "WHERE source_id = :id ORDER BY created_at"
                    ),
                    {"id": credit_note_id},
                )
            )
            .mappings()
            .all()
        )
    await engine.dispose()

    assert updated["status"] == "reversed"
    assert updated["number"] == original["number"]
    assert updated["amount"] == original["amount"]
    assert updated["reversal_ledger_entry_id"] is not None
    assert [(e["entry_type"], e["source_type"], Decimal(e["amount"])) for e in entries] == [
        ("credit_note", "credit_note", Decimal("-50.00")),
        ("credit_note", "credit_note_reversal", Decimal("50.00")),
    ]


@pytest.mark.asyncio
async def test_request_against_voided_invoice_is_rejected(
    invariant_client: AsyncClient,
    invariant_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    _, draft_invoice_id = await _posted_invoice(
        invariant_client, invariant_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)
    void = await invariant_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/void",
        headers=auth(
            invariant_settings,
            "finance-recorder",
            **{"Idempotency-Key": "invariant-void-invoice"},
        ),
        json={"reason": "Customer refused."},
    )
    assert void.status_code == 201, void.text

    request = await invariant_client.post(
        f"/v1/finance/invoices/{draft_invoice_id}/credit-notes",
        headers=auth(
            invariant_settings,
            "finance-recorder",
            **{"Idempotency-Key": "invariant-request-voided"},
        ),
        json={"amount": "10.00", "currency": "PHP", "reason": "After void."},
    )
    assert request.status_code == 409, request.text
    assert request.json()["error"]["code"] == "invoice_not_creditable"


@pytest.mark.asyncio
async def test_concurrent_posts_are_serialized(
    invariant_client: AsyncClient,
    invariant_settings: Settings,
    postgres_url: str,
    fake_storage: FakeObjectStorage,
) -> None:
    _, draft_invoice_id = await _posted_invoice(
        invariant_client, invariant_settings, postgres_url, fake_storage
    )
    await _setup_credit_note_roles_and_authority(postgres_url)
    await _seed_credit_note_document_series(postgres_url)
    requested = await _request_credit_note(
        invariant_client,
        invariant_settings,
        draft_invoice_id,
        amount="10.00",
        idempotency_key="concurrent-request",
    )
    credit_note_id = requested["credit_note_id"]

    import asyncio

    async def post_attempt(key: str) -> object:
        return await invariant_client.post(
            f"/v1/finance/credit-notes/{credit_note_id}/post",
            headers=auth(
                invariant_settings,
                "finance-verifier",
                **{"Idempotency-Key": key},
            ),
            json={},
        )

    results = await asyncio.gather(
        post_attempt("concurrent-post-a"),
        post_attempt("concurrent-post-b"),
    )
    statuses = [r.status_code for r in results]
    assert statuses.count(201) == 1
    assert statuses.count(409) == 1
