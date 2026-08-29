from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_delivery_confirmation_contract import (
    confirmation_client as confirmation_client,
)
from test_delivery_confirmation_contract import (
    confirmation_settings as confirmation_settings,
)
from test_delivery_confirmation_contract import fake_storage as fake_storage
from test_delivery_correction_contract import _confirm_fully_accepted_delivery
from test_payment_clearance_contract import auth
from test_return_authorization_contract import _grant_return_capabilities
from tradeflow_api.config import Settings


async def _grant_return_receipt_capabilities(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO capabilities(code)
                VALUES ('returns:receive')
                ON CONFLICT (code) DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO role_template_capabilities(role_template_id, capability_code)
                SELECT role_template_id, 'returns:receive'
                FROM role_templates
                WHERE code = 'WAREHOUSE_SUPERVISOR'
                ON CONFLICT DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO approval_authorities(
                  approval_authority_id, user_subject, capability_code, branch_id,
                  warehouse_id, maximum_amount, maximum_percentage, maker_checker_required
                )
                SELECT :authority_id, 'delivery-correction-checker-mnl',
                       'returns:receive', branch.branch_id, warehouse.warehouse_id,
                       1000.00, NULL, true
                FROM branches branch
                JOIN warehouses warehouse ON warehouse.branch_id = branch.branch_id
                WHERE branch.code = 'MNL' AND warehouse.code = 'MNL-01'
                """
            ),
            {"authority_id": uuid4()},
        )
        await connection.execute(
            text(
                """
                INSERT INTO approval_authorities(
                  approval_authority_id, user_subject, capability_code, branch_id,
                  warehouse_id, maximum_amount, maximum_percentage, maker_checker_required
                )
                SELECT :authority_id, 'delivery-correction-checker-low-mnl',
                       'returns:receive', branch.branch_id, warehouse.warehouse_id,
                       1.00, NULL, true
                FROM branches branch
                JOIN warehouses warehouse ON warehouse.branch_id = branch.branch_id
                WHERE branch.code = 'MNL' AND warehouse.code = 'MNL-01'
                """
            ),
            {"authority_id": uuid4()},
        )
    await engine.dispose()


async def _create_authorized_return_request(
    client: AsyncClient,
    settings: Settings,
    postgres_url: str,
) -> tuple[dict[str, object], dict[str, object], str, dict[str, object]]:
    fixture, confirmation = await _confirm_fully_accepted_delivery(client, settings, postgres_url)
    await _grant_return_capabilities(postgres_url)
    await _grant_return_receipt_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())
    created = await client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": f"receipt-return-request-{request_id}"},
        ),
        json={
            "return_request_id": request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "quantity_base": "1.000000",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    request_payload = created.json()
    authorized = await client.post(
        f"/v1/return-requests/{request_id}/authorization",
        headers=auth(
            settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": f"receipt-return-authorization-{request_id}"},
        ),
        json={"expected_request_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    return fixture, confirmation, request_id, request_payload


async def _upload_return_evidence(
    client: AsyncClient,
    settings: Settings,
    request_id: str,
    actor_subject: str,
    evidence_id: str,
) -> None:
    upload = await client.post(
        f"/v1/return-requests/{request_id}/evidence/uploads",
        headers=auth(settings, actor_subject),
        json={
            "evidence_id": evidence_id,
            "kind": "photo",
            "content_type": "image/png",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "device_captured_at": "2026-08-01T13:00:00Z",
        },
    )
    assert upload.status_code == 201, upload.text
    completed = await client.post(
        f"/v1/return-requests/{request_id}/evidence/{evidence_id}/complete",
        headers=auth(settings, actor_subject),
    )
    assert completed.status_code == 200, completed.text


async def _post_receipt(
    client: AsyncClient,
    settings: Settings,
    request_id: str,
    evidence_ids: list[str],
    lines: list[dict[str, object]],
    receiver_subject: str,
    receipt_id: str,
    idempotency_key: str,
) -> dict[str, object]:
    response = await client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            settings,
            receiver_subject,
            **{"Idempotency-Key": idempotency_key},
        ),
        json={
            "return_receipt_id": receipt_id,
            "expected_request_version": 2,
            "received_at": "2026-08-01T13:00:00Z",
            "notes": "Inspection complete.",
            "evidence_ids": evidence_ids,
            "lines": lines,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.mark.asyncio
async def test_restock_receipt_moves_stock_to_available_and_updates_valuation(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture, _confirmation, request_id, request_payload = await _create_authorized_return_request(
        confirmation_client, confirmation_settings, postgres_url
    )
    evidence_id = str(uuid4())
    await _upload_return_evidence(
        confirmation_client,
        confirmation_settings,
        request_id,
        "delivery-correction-checker-mnl",
        evidence_id,
    )
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        before = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM stock_movements) AS stock_movements,
                          (SELECT COALESCE(SUM(ia.on_hand), 0) FROM inventory_availability ia
                            JOIN warehouse_stock_locations loc
                              ON loc.location_id = ia.location_id
                            WHERE ia.sku_id = :sku_id AND ia.warehouse_id = :warehouse_id
                              AND loc.custody = 'available') AS available,
                          (SELECT moving_average_unit_cost FROM inventory_valuation
                            WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id) AS unit_cost,
                          (SELECT inventory_value FROM inventory_valuation
                            WHERE sku_id = :sku_id
                              AND warehouse_id = :warehouse_id) AS inventory_value
                        """
                    ),
                    {
                        "sku_id": fixture["sku_id"],
                        "warehouse_id": fixture["warehouse_id"],
                    },
                )
            )
            .mappings()
            .one()
        )

    receipt_id = str(uuid4())
    response = await _post_receipt(
        confirmation_client,
        confirmation_settings,
        request_id,
        [evidence_id],
        [
            {
                "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
                "received_quantity_base": "1.000000",
                "outcome": "restock",
                "notes": "Resealed carton.",
            }
        ],
        "delivery-correction-checker-mnl",
        receipt_id,
        f"receipt-restock-{receipt_id}",
    )
    assert response["status"] == "received"
    assert response["version"] == 3
    assert response["received_by"] == "delivery-correction-checker-mnl"
    line = response["lines"][0]
    assert line["outcome"] == "restock"
    assert line["custody"] == "available"
    assert line["movement_id"] is not None

    async with engine.connect() as connection:
        after = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM stock_movements) AS stock_movements,
                          (SELECT COALESCE(SUM(ia.on_hand), 0) FROM inventory_availability ia
                            JOIN warehouse_stock_locations loc
                              ON loc.location_id = ia.location_id
                            WHERE ia.sku_id = :sku_id AND ia.warehouse_id = :warehouse_id
                              AND loc.custody = 'available') AS available,
                          (SELECT inventory_value FROM inventory_valuation
                            WHERE sku_id = :sku_id
                              AND warehouse_id = :warehouse_id) AS inventory_value
                        """
                    ),
                    {
                        "sku_id": fixture["sku_id"],
                        "warehouse_id": fixture["warehouse_id"],
                    },
                )
            )
            .mappings()
            .one()
        )
        detail = await connection.execute(
            text(
                """
                SELECT * FROM stock_movements
                WHERE source_reference = :source_reference
                """
            ),
            {"source_reference": f"RETURN-RECEIPT:{receipt_id}"},
        )
        movement = detail.mappings().one()
    await engine.dispose()

    assert after["stock_movements"] == before["stock_movements"] + 1
    assert Decimal(after["available"]) == Decimal(before["available"]) + Decimal("1")
    unit_cost = Decimal(before["unit_cost"])
    expected_value_delta = (unit_cost * Decimal("1")).quantize(Decimal("0.000001"))
    assert Decimal(movement["value_delta"]) == expected_value_delta
    assert (
        Decimal(after["inventory_value"])
        == Decimal(before["inventory_value"]) + expected_value_delta
    )

    listing = await confirmation_client.get(
        "/v1/return-requests?status=received",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["return_request_id"] == request_id
    assert listing.json()["items"][0]["return_receipt_id"] == receipt_id


@pytest.mark.asyncio
async def test_quarantine_and_damaged_outcomes_skip_valuation(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    await _grant_return_capabilities(postgres_url)
    await _grant_return_receipt_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    delivery_line_id = confirmation["lines"][0]["delivery_line_id"]

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        before_value = await connection.scalar(
            text(
                """
                    SELECT inventory_value FROM inventory_valuation
                    WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                    """
            ),
            {"sku_id": fixture["sku_id"], "warehouse_id": fixture["warehouse_id"]},
        )

    for outcome in ("quarantine", "damaged"):
        request_id = str(uuid4())
        created = await confirmation_client.post(
            f"/v1/delivery-receipts/{receipt_id}/return-requests",
            headers=auth(
                confirmation_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": f"receipt-return-request-{outcome}-{request_id}"},
            ),
            json={
                "return_request_id": request_id,
                "reason_code": "PRODUCT_DEFECT",
                "reason_label": "Product defect",
                "responsible_party_code": "SUPPLIER",
                "responsible_party_label": "Supplier",
                "lines": [{"delivery_line_id": delivery_line_id, "quantity_base": "1.000000"}],
            },
        )
        assert created.status_code == 201, created.text
        request_payload = created.json()
        authorized = await confirmation_client.post(
            f"/v1/return-requests/{request_id}/authorization",
            headers=auth(
                confirmation_settings,
                "delivery-correction-checker-mnl",
                **{"Idempotency-Key": f"receipt-return-authorization-{outcome}-{request_id}"},
            ),
            json={"expected_request_version": 1},
        )
        assert authorized.status_code == 200, authorized.text

        evidence_id = str(uuid4())
        await _upload_return_evidence(
            confirmation_client,
            confirmation_settings,
            request_id,
            "delivery-correction-checker-mnl",
            evidence_id,
        )
        receipt_uuid = str(uuid4())
        response = await _post_receipt(
            confirmation_client,
            confirmation_settings,
            request_id,
            [evidence_id],
            [
                {
                    "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
                    "received_quantity_base": "1.000000",
                    "outcome": outcome,
                }
            ],
            "delivery-correction-checker-mnl",
            receipt_uuid,
            f"receipt-{outcome}-{receipt_uuid}",
        )
        assert response["lines"][0]["custody"] == "quarantine"
        assert response["lines"][0]["movement_id"] is not None
        async with engine.connect() as connection:
            after_value = await connection.scalar(
                text(
                    """
                    SELECT inventory_value FROM inventory_valuation
                    WHERE sku_id = :sku_id AND warehouse_id = :warehouse_id
                    """
                ),
                {"sku_id": fixture["sku_id"], "warehouse_id": fixture["warehouse_id"]},
            )
        assert Decimal(after_value) == Decimal(before_value)
    await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_line_creates_no_movement(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture, confirmation, request_id, request_payload = await _create_authorized_return_request(
        confirmation_client, confirmation_settings, postgres_url
    )
    evidence_id = str(uuid4())
    await _upload_return_evidence(
        confirmation_client,
        confirmation_settings,
        request_id,
        "delivery-correction-checker-mnl",
        evidence_id,
    )

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        before = await connection.scalar(text("SELECT count(*) FROM stock_movements"))

    receipt_id = str(uuid4())
    response = await _post_receipt(
        confirmation_client,
        confirmation_settings,
        request_id,
        [evidence_id],
        [
            {
                "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
                "received_quantity_base": "0",
                "outcome": "rejected",
            }
        ],
        "delivery-correction-checker-mnl",
        receipt_id,
        f"receipt-rejected-{receipt_id}",
    )
    assert response["lines"][0]["outcome"] == "rejected"
    assert response["lines"][0]["movement_id"] is None
    assert response["lines"][0]["custody"] is None

    async with engine.connect() as connection:
        after = await connection.scalar(text("SELECT count(*) FROM stock_movements"))
    await engine.dispose()
    assert after == before


@pytest.mark.asyncio
async def test_receipt_is_idempotent_and_blocks_duplicate_receipt(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _fixture, confirmation, request_id, request_payload = await _create_authorized_return_request(
        confirmation_client, confirmation_settings, postgres_url
    )
    evidence_id = str(uuid4())
    await _upload_return_evidence(
        confirmation_client,
        confirmation_settings,
        request_id,
        "delivery-correction-checker-mnl",
        evidence_id,
    )
    receipt_id = str(uuid4())
    key = f"receipt-idempotent-{receipt_id}"
    first = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": key},
        ),
        json={
            "return_receipt_id": receipt_id,
            "expected_request_version": 2,
            "received_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
                    "received_quantity_base": "1.000000",
                    "outcome": "restock",
                }
            ],
        },
    )
    assert first.status_code == 201, first.text
    assert first.headers["X-Idempotency-Replayed"] == "false"

    replay = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": key},
        ),
        json={
            "return_receipt_id": receipt_id,
            "expected_request_version": 2,
            "received_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
                    "received_quantity_base": "1.000000",
                    "outcome": "restock",
                }
            ],
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.headers["X-Idempotency-Replayed"] == "true"
    assert replay.json() == first.json()

    second_receipt_id = str(uuid4())
    duplicate = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": f"receipt-duplicate-{second_receipt_id}"},
        ),
        json={
            "return_receipt_id": second_receipt_id,
            "expected_request_version": 2,
            "received_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
                    "received_quantity_base": "1.000000",
                    "outcome": "restock",
                }
            ],
        },
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "return_request_already_receipted"


@pytest.mark.asyncio
async def test_receiver_must_have_scope_and_cannot_be_requester(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _fixture, confirmation, request_id, request_payload = await _create_authorized_return_request(
        confirmation_client, confirmation_settings, postgres_url
    )
    evidence_id = str(uuid4())
    await _upload_return_evidence(
        confirmation_client,
        confirmation_settings,
        request_id,
        "delivery-correction-checker-mnl",
        evidence_id,
    )
    receipt_id = str(uuid4())
    body = {
        "return_receipt_id": receipt_id,
        "expected_request_version": 2,
        "received_at": "2026-08-01T13:00:00Z",
        "evidence_ids": [evidence_id],
        "lines": [
            {
                "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
                "received_quantity_base": "1.000000",
                "outcome": "restock",
            }
        ],
    }
    no_capability = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "warehouse-mnl",
            **{"Idempotency-Key": f"receipt-no-capability-{receipt_id}"},
        ),
        json=body,
    )
    assert no_capability.status_code == 403, no_capability.text
    assert no_capability.json()["error"]["code"] == "capability_required"

    self_receive = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": f"receipt-self-{receipt_id}"},
        ),
        json=body,
    )
    assert self_receive.status_code == 403, self_receive.text
    assert self_receive.json()["error"]["code"] == "maker_checker_violation"


@pytest.mark.asyncio
async def test_receipt_validates_version_quantity_evidence_and_authority(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _fixture, confirmation, request_id, request_payload = await _create_authorized_return_request(
        confirmation_client, confirmation_settings, postgres_url
    )
    evidence_id = str(uuid4())
    await _upload_return_evidence(
        confirmation_client,
        confirmation_settings,
        request_id,
        "delivery-correction-checker-mnl",
        evidence_id,
    )
    line = {
        "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
        "received_quantity_base": "1.000000",
        "outcome": "restock",
    }

    version_conflict = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": f"receipt-version-{uuid4()}"},
        ),
        json={
            "return_receipt_id": str(uuid4()),
            "expected_request_version": 1,
            "received_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [line],
        },
    )
    assert version_conflict.status_code == 409, version_conflict.text
    assert version_conflict.json()["error"]["code"] == "return_request_version_conflict"

    quantity_excess = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": f"receipt-quantity-{uuid4()}"},
        ),
        json={
            "return_receipt_id": str(uuid4()),
            "expected_request_version": 2,
            "received_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [
                {
                    **line,
                    "received_quantity_base": "2.000000",
                }
            ],
        },
    )
    assert quantity_excess.status_code == 409, quantity_excess.text
    assert quantity_excess.json()["error"]["code"] == "return_quantity_exceeds_authorized"

    no_evidence = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": f"receipt-no-evidence-{uuid4()}"},
        ),
        json={
            "return_receipt_id": str(uuid4()),
            "expected_request_version": 2,
            "received_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [],
            "lines": [line],
        },
    )
    assert no_evidence.status_code == 409, no_evidence.text
    assert no_evidence.json()["error"]["code"] == "return_evidence_conflict"

    under_limit = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-low-mnl",
            **{"Idempotency-Key": f"receipt-under-limit-{uuid4()}"},
        ),
        json={
            "return_receipt_id": str(uuid4()),
            "expected_request_version": 2,
            "received_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [evidence_id],
            "lines": [line],
        },
    )
    assert under_limit.status_code == 403, under_limit.text
    assert under_limit.json()["error"]["code"] == "approval_authority_required"


@pytest.mark.asyncio
async def test_evidence_must_belong_to_request_and_be_verified(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _fixture, confirmation, request_id, request_payload = await _create_authorized_return_request(
        confirmation_client, confirmation_settings, postgres_url
    )
    other_request_id = str(uuid4())
    other_receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    other_created = await confirmation_client.post(
        f"/v1/delivery-receipts/{other_receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": f"other-request-{other_request_id}"},
        ),
        json={
            "return_request_id": other_request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "quantity_base": "1.000000",
                }
            ],
        },
    )
    assert other_created.status_code == 201, other_created.text
    other_authorized = await confirmation_client.post(
        f"/v1/return-requests/{other_request_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": f"other-authorization-{other_request_id}"},
        ),
        json={"expected_request_version": 1},
    )
    assert other_authorized.status_code == 200, other_authorized.text
    other_evidence_id = str(uuid4())
    await _upload_return_evidence(
        confirmation_client,
        confirmation_settings,
        other_request_id,
        "delivery-correction-checker-mnl",
        other_evidence_id,
    )

    receipt_id = str(uuid4())
    wrong_evidence = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/receipts",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": f"receipt-wrong-evidence-{receipt_id}"},
        ),
        json={
            "return_receipt_id": receipt_id,
            "expected_request_version": 2,
            "received_at": "2026-08-01T13:00:00Z",
            "evidence_ids": [other_evidence_id],
            "lines": [
                {
                    "return_request_line_id": request_payload["lines"][0]["return_request_line_id"],
                    "received_quantity_base": "1.000000",
                    "outcome": "restock",
                }
            ],
        },
    )
    assert wrong_evidence.status_code == 409, wrong_evidence.text
    assert wrong_evidence.json()["error"]["code"] == "return_evidence_conflict"
