from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_delivery_correction_contract import _confirm_fully_accepted_delivery
from test_payment_clearance_contract import (
    approved_prepaid_order,
    auth,
)
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def cancellation_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def cancellation_client(
    cancellation_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    app = create_app(cancellation_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _ensure_cancellation_capability(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO capabilities (code) VALUES ('sales:order-cancel') "
                "ON CONFLICT (code) DO NOTHING"
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO role_template_capabilities (role_template_id, capability_code)
                SELECT role_template_id, 'sales:order-cancel'
                  FROM role_templates
                 WHERE code = 'SALES'
                ON CONFLICT (role_template_id, capability_code) DO NOTHING
                """
            )
        )
    await engine.dispose()


async def _grant_cancellation_authority(
    postgres_url: str,
    *,
    subject: str,
    branch_id: str,
    warehouse_id: str | None,
    maximum_amount: str = "1000000.00",
    maker_checker_required: bool = False,
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO approval_authorities (
                    approval_authority_id,
                    user_subject,
                    capability_code,
                    branch_id,
                    warehouse_id,
                    maximum_amount,
                    maximum_percentage,
                    maker_checker_required
                )
                VALUES (
                    :id,
                    :subject,
                    'sales:order-cancel',
                    :branch_id,
                    :warehouse_id,
                    :maximum_amount,
                    NULL,
                    :maker_checker_required
                )
                """
            ),
            {
                "id": uuid4(),
                "subject": subject,
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
                "maximum_amount": maximum_amount,
                "maker_checker_required": maker_checker_required,
            },
        )
    await engine.dispose()


async def _create_canceller(
    postgres_url: str,
    *,
    subject: str,
    display_name: str,
    role_code: str,
    branch_ids: list[str],
    warehouse_ids: list[str],
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (subject, display_name) "
                "VALUES (:subject, :display_name) "
                "ON CONFLICT (subject) DO NOTHING"
            ),
            {"subject": subject, "display_name": display_name},
        )
        role_id = await connection.scalar(
            text("SELECT role_template_id FROM role_templates WHERE code = :code"),
            {"code": role_code},
        )
        await connection.execute(
            text(
                "INSERT INTO user_role_templates (user_subject, role_template_id) "
                "VALUES (:subject, :role_id) "
                "ON CONFLICT (user_subject, role_template_id) DO NOTHING"
            ),
            {"subject": subject, "role_id": role_id},
        )
        for branch_id in branch_ids:
            await connection.execute(
                text(
                    "INSERT INTO user_branch_scopes (user_subject, branch_id) "
                    "VALUES (:subject, :branch_id) "
                    "ON CONFLICT (user_subject, branch_id) DO NOTHING"
                ),
                {"subject": subject, "branch_id": branch_id},
            )
        for warehouse_id in warehouse_ids:
            await connection.execute(
                text(
                    "INSERT INTO user_warehouse_scopes (user_subject, warehouse_id) "
                    "VALUES (:subject, :warehouse_id) "
                    "ON CONFLICT (user_subject, warehouse_id) DO NOTHING"
                ),
                {"subject": subject, "warehouse_id": warehouse_id},
            )
    await engine.dispose()


async def _order_version(
    client: AsyncClient,
    settings: Settings,
    sales_order_id: str,
    actor: str = "sales-mnl",
) -> dict[str, Any]:
    response = await client.get(
        f"/v1/sales/orders/{sales_order_id}",
        headers=auth(settings, actor),
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def _cancel(
    client: AsyncClient,
    settings: Settings,
    sales_order_id: str,
    *,
    lines: list[dict[str, object]],
    reason: str,
    if_match: int,
    key: str,
    actor: str = "sales-mnl",
) -> Response:
    response = await client.post(
        f"/v1/sales/orders/{sales_order_id}/cancellation",
        headers=auth(
            settings,
            actor,
            **{"Idempotency-Key": key, "If-Match": str(if_match)},
        ),
        json={"lines": lines, "reason": reason},
    )
    return response


@pytest.mark.asyncio
async def test_partial_cancellation_releases_reservation_and_reduces_backorder(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)
    await _grant_cancellation_authority(
        postgres_url,
        subject="sales-mnl",
        branch_id=str(fixture["branch_id"]),
        warehouse_id=str(fixture["warehouse_id"]),
    )

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "1.000000",
            }
        ],
        reason="Customer requested partial cancellation.",
        if_match=cast(int, order["metadata_version"]),
        key="partial-cancel-1",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "partially_cancelled"
    assert body["lines"][0]["cancelled_quantity_base"] == "1.000000"
    assert body["lines"][0]["reserved_released_quantity_base"] == "1.000000"
    assert body["lines"][0]["backorder_reduced_quantity_base"] == "0.000000"
    assert body["reserved_released_quantity_base"] == "1.000000"
    assert body["backorder_reduced_quantity_base"] == "0.000000"

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        commitment = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT reserved_quantity_base,
                               backorder_quantity_base,
                               cancelled_quantity_base
                          FROM sales_order_line_commitments
                         WHERE sales_order_id = :sales_order_id
                           AND line_id = :line_id
                        """
                    ),
                    {
                        "sales_order_id": fixture["sales_order_id"],
                        "line_id": fixture["line_id"],
                    },
                )
            )
            .mappings()
            .one()
        )
        reserved_stock = await connection.scalar(
            text(
                """
                SELECT reserved_quantity_base
                  FROM inventory_reserved_by_sku_warehouse
                 WHERE sku_id = :sku_id
                   AND warehouse_id = :warehouse_id
                """
            ),
            {
                "sku_id": fixture["sku_id"],
                "warehouse_id": fixture["warehouse_id"],
            },
        )
    await engine.dispose()

    assert Decimal(commitment["reserved_quantity_base"]) == Decimal("1.000000")
    assert Decimal(commitment["backorder_quantity_base"]) == Decimal("1.000000")
    assert Decimal(commitment["cancelled_quantity_base"]) == Decimal("1.000000")
    assert Decimal(reserved_stock) == Decimal("1.000000")


@pytest.mark.asyncio
async def test_full_cancellation_cancels_sales_order_and_fulfillment(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)
    await _grant_cancellation_authority(
        postgres_url,
        subject="sales-mnl",
        branch_id=str(fixture["branch_id"]),
        warehouse_id=str(fixture["warehouse_id"]),
    )

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "3.000000",
            }
        ],
        reason="Customer cancelled entire order.",
        if_match=cast(int, order["metadata_version"]),
        key="full-cancel-1",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["total_cancelled_quantity_base"] == "3.000000"
    assert body["reserved_released_quantity_base"] == "2.000000"
    assert body["backorder_reduced_quantity_base"] == "1.000000"

    fulfillment_order_id = cast(
        str,
        cast(dict[str, object], fixture["fulfillment_order"])["fulfillment_order_id"],
    )

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        order_state = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT status,
                               reserved_quantity_base,
                               backorder_quantity_base,
                               payment_hold
                          FROM fulfillment_order_state
                         WHERE fulfillment_order_id = :fulfillment_order_id
                        """
                    ),
                    {"fulfillment_order_id": fulfillment_order_id},
                )
            )
            .mappings()
            .one()
        )
        hold = await connection.scalar(
            text(
                """
                SELECT COUNT(*)
                  FROM active_sales_order_holds
                 WHERE sales_order_id = :sales_order_id
                """
            ),
            {"sales_order_id": fixture["sales_order_id"]},
        )
    await engine.dispose()

    assert order_state["status"] == "cancelled"
    assert Decimal(order_state["reserved_quantity_base"]) == Decimal("0")
    assert Decimal(order_state["backorder_quantity_base"]) == Decimal("0")
    assert order_state["payment_hold"] is False
    assert hold == 0


@pytest.mark.asyncio
async def test_cancellation_cannot_exceed_open_quantity_after_delivery(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture, _ = await _confirm_fully_accepted_delivery(
        cancellation_client,
        cancellation_settings,
        postgres_url,
    )
    await _ensure_cancellation_capability(postgres_url)
    await _grant_cancellation_authority(
        postgres_url,
        subject="sales-mnl",
        branch_id=str(fixture["branch_id"]),
        warehouse_id=str(fixture["warehouse_id"]),
    )

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "2.000000",
            }
        ],
        reason="Trying to cancel delivered quantity.",
        if_match=cast(int, order["metadata_version"]),
        key="cancel-delivered",
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "cancel_quantity_exceeds_open"


@pytest.mark.asyncio
async def test_cancellation_requires_approval_authority(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "1.000000",
            }
        ],
        reason="No authority.",
        if_match=cast(int, order["metadata_version"]),
        key="cancel-no-authority",
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "approval_authority_required"


@pytest.mark.asyncio
async def test_cancellation_enforces_branch_scope(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        ceb = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT b.branch_id, w.warehouse_id
                          FROM branches b
                          JOIN warehouses w ON w.branch_id = b.branch_id
                         WHERE b.code = 'CEB'
                           AND w.code = 'CEB-01'
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    await _create_canceller(
        postgres_url,
        subject="canceller-ceb",
        display_name="Cebu Canceller",
        role_code="SALES",
        branch_ids=[str(ceb["branch_id"])],
        warehouse_ids=[str(ceb["warehouse_id"])],
    )
    await _grant_cancellation_authority(
        postgres_url,
        subject="canceller-ceb",
        branch_id=cast(str, ceb["branch_id"]),
        warehouse_id=cast(str, ceb["warehouse_id"]),
    )

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "1.000000",
            }
        ],
        reason="Cross branch cancellation.",
        if_match=cast(int, order["metadata_version"]),
        key="cancel-cross-branch",
        actor="canceller-ceb",
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "operational_scope_required"


@pytest.mark.asyncio
async def test_cancellation_enforces_warehouse_scope(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)
    await _create_canceller(
        postgres_url,
        subject="canceller-no-warehouse",
        display_name="No Warehouse Canceller",
        role_code="SALES",
        branch_ids=[str(fixture["branch_id"])],
        warehouse_ids=[],
    )
    await _grant_cancellation_authority(
        postgres_url,
        subject="canceller-no-warehouse",
        branch_id=str(fixture["branch_id"]),
        warehouse_id=None,
    )

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "1.000000",
            }
        ],
        reason="Missing warehouse scope.",
        if_match=cast(int, order["metadata_version"]),
        key="cancel-no-warehouse",
        actor="canceller-no-warehouse",
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "warehouse_scope_required"


@pytest.mark.asyncio
async def test_cancellation_maker_checker_violation(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)
    await _grant_cancellation_authority(
        postgres_url,
        subject="sales-mnl",
        branch_id=str(fixture["branch_id"]),
        warehouse_id=str(fixture["warehouse_id"]),
        maker_checker_required=True,
    )

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "1.000000",
            }
        ],
        reason="Maker cannot cancel own order.",
        if_match=cast(int, order["metadata_version"]),
        key="cancel-maker-checker",
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "maker_checker_violation"


@pytest.mark.asyncio
async def test_cancellation_idempotent_replay_returns_same_result(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)
    await _grant_cancellation_authority(
        postgres_url,
        subject="sales-mnl",
        branch_id=str(fixture["branch_id"]),
        warehouse_id=str(fixture["warehouse_id"]),
    )

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response1 = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "1.000000",
            }
        ],
        reason="Idempotent cancellation.",
        if_match=cast(int, order["metadata_version"]),
        key="cancel-idempotent",
    )
    assert response1.status_code == 201, response1.text

    response2 = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "1.000000",
            }
        ],
        reason="Idempotent cancellation.",
        if_match=cast(int, order["metadata_version"]),
        key="cancel-idempotent",
    )
    assert response2.status_code == 200, response2.text
    assert response1.json() == response2.json()


@pytest.mark.asyncio
async def test_on_account_cancellation_releases_credit_exposure(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(
        cancellation_client,
        cancellation_settings,
        postgres_url,
        payment_timing_policy="on_account",
    )
    await _ensure_cancellation_capability(postgres_url)
    await _grant_cancellation_authority(
        postgres_url,
        subject="sales-mnl",
        branch_id=str(fixture["branch_id"]),
        warehouse_id=str(fixture["warehouse_id"]),
    )

    order = await _order_version(
        cancellation_client, cancellation_settings, str(fixture["sales_order_id"])
    )
    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "3.000000",
            }
        ],
        reason="On-account order cancelled.",
        if_match=cast(int, order["metadata_version"]),
        key="cancel-on-account",
    )
    assert response.status_code == 201, response.text
    assert response.json()["credit_released_base"] == "336.00"

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        exposure = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT approved_uninvoiced
                          FROM customer_credit_exposure
                         WHERE customer_id = :customer_id
                        """
                    ),
                    {"customer_id": fixture["customer_id"]},
                )
            )
            .mappings()
            .one()
        )
        credit_entry = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT amount_delta
                          FROM credit_exposure_entries
                         WHERE customer_id = :customer_id
                           AND component = 'approved_uninvoiced'
                           AND source_type = 'sales_order_cancellation'
                        """
                    ),
                    {"customer_id": fixture["customer_id"]},
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()

    assert Decimal(exposure["approved_uninvoiced"]) == Decimal("0")
    assert Decimal(credit_entry["amount_delta"]) == Decimal("-336.00")


@pytest.mark.asyncio
async def test_cancellation_respects_optimistic_version(
    cancellation_client: AsyncClient,
    cancellation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture = await approved_prepaid_order(cancellation_client, cancellation_settings, postgres_url)
    await _ensure_cancellation_capability(postgres_url)
    await _grant_cancellation_authority(
        postgres_url,
        subject="sales-mnl",
        branch_id=str(fixture["branch_id"]),
        warehouse_id=str(fixture["warehouse_id"]),
    )

    response = await _cancel(
        cancellation_client,
        cancellation_settings,
        str(fixture["sales_order_id"]),
        lines=[
            {
                "line_id": fixture["line_id"],
                "cancel_quantity_base": "1.000000",
            }
        ],
        reason="Stale version.",
        if_match=1,
        key="cancel-stale-version",
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "optimistic_version_conflict"
