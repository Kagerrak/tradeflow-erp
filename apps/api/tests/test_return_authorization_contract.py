from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_delivery_confirmation_contract import (
    FakeObjectStorage,
)
from test_delivery_confirmation_contract import (
    confirmation_client as confirmation_client,
)
from test_delivery_confirmation_contract import (
    confirmation_settings as confirmation_settings,
)
from test_delivery_confirmation_contract import fake_storage as fake_storage
from test_delivery_correction_contract import _confirm_fully_accepted_delivery
from test_payment_clearance_contract import auth
from tradeflow_api.config import Settings
from tradeflow_worker.worker import poll_delivery_confirmation_outbox


async def _grant_return_capabilities(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO return_reasons(code,label)
                VALUES ('PRODUCT_DEFECT','Product defect'), ('WRONG_ITEM','Wrong item')
                ON CONFLICT DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO return_responsible_parties(code,label)
                VALUES ('SUPPLIER','Supplier'), ('CUSTOMER','Customer')
                ON CONFLICT DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO capabilities(code)
                VALUES
                  ('returns:request'),
                  ('returns:authorize')
                ON CONFLICT (code) DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO role_template_capabilities(role_template_id, capability_code)
                SELECT role_template_id, capability.code
                FROM role_templates
                CROSS JOIN (
                  VALUES ('returns:request'), ('returns:authorize')
                ) AS capability(code)
                WHERE role_templates.code = 'WAREHOUSE_SUPERVISOR'
                ON CONFLICT DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO approval_authorities(
                  approval_authority_id, user_subject, capability_code, branch_id,
                  maximum_amount, maximum_percentage, maker_checker_required
                )
                SELECT :authority_id, 'delivery-correction-checker-mnl',
                       'returns:authorize', branch_id, 1000.00, NULL, true
                FROM branches WHERE code = 'MNL'
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
                SELECT :authority_id, 'delivery-correction-checker-mnl',
                       'returns:authorize', branch.branch_id, warehouse.warehouse_id,
                       1.00, NULL, true
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
                  maximum_amount, maximum_percentage, maker_checker_required
                )
                SELECT :authority_id, 'delivery-correction-checker-low-mnl',
                       'returns:authorize', branch_id, 1.00, NULL, true
                FROM branches WHERE code = 'MNL'
                """
            ),
            {"authority_id": uuid4()},
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_requester_records_return_request_without_stock_or_financial_effects(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    fixture, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    await _grant_return_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())
    classifications = await confirmation_client.get(
        "/v1/return-classifications",
        headers=auth(confirmation_settings, "warehouse-supervisor-mnl"),
    )
    assert classifications.status_code == 200, classifications.text
    assert {item["code"] for item in classifications.json()["reasons"]} >= {
        "PRODUCT_DEFECT",
        "WRONG_ITEM",
    }
    assert {item["code"] for item in classifications.json()["responsible_parties"]} >= {
        "CUSTOMER",
        "SUPPLIER",
    }
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        before_effects = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM stock_movements) AS stock_movements,
                          (SELECT count(*) FROM customer_ledger_entries) AS ledger_entries,
                          (SELECT count(*) FROM draft_invoices) AS draft_invoices,
                          (SELECT count(*) FROM outbox_events) AS outbox_events
                        """
                    )
                )
            )
            .mappings()
            .one()
        )

    invalid_classification = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "reject-invented-return-classification"},
        ),
        json={
            "return_request_id": str(uuid4()),
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Invented label",
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
    assert invalid_classification.status_code == 409, invalid_classification.text
    assert invalid_classification.json()["error"]["code"] == "return_classification_invalid"

    response = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "record-return-request"},
        ),
        json={
            "return_request_id": request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "notes": "Customer reported a sealed-unit defect.",
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "quantity_base": "1.000000",
                }
            ],
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["return_request_id"] == request_id
    assert payload["delivery_receipt_id"] == receipt_id
    assert payload["status"] == "pending_authorization"
    assert payload["version"] == 1
    assert payload["requested_by"] == "warehouse-supervisor-mnl"
    assert payload["authorized_by"] is None
    assert payload["reason_code"] == "PRODUCT_DEFECT"
    assert payload["reason_label"] == "Product defect"
    assert payload["responsible_party_code"] == "SUPPLIER"
    assert payload["responsible_party_label"] == "Supplier"
    # The fixture line total is PHP 224 for two units after discount and tax.
    assert Decimal(payload["affected_value_base_currency"]) == Decimal("112")
    assert payload["base_currency"] == "PHP"
    assert payload["lines"] == [
        {
            "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
            "line_id": fixture["line_id"],
            "sku_id": fixture["sku_id"],
            "quantity_base": "1.000000",
            "delivered_quantity_base": "2.000000",
            "eligible_quantity_base": "2.000000",
        }
    ]

    async with engine.connect() as connection:
        after_effects = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM stock_movements) AS stock_movements,
                          (SELECT count(*) FROM customer_ledger_entries) AS ledger_entries,
                          (SELECT count(*) FROM draft_invoices) AS draft_invoices,
                          (SELECT count(*) FROM outbox_events) AS outbox_events
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert dict(after_effects) == dict(before_effects)


@pytest.mark.asyncio
async def test_distinct_approver_reserves_only_remaining_delivered_quantity_and_replays(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    await _grant_return_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    delivery_line_id = confirmation["lines"][0]["delivery_line_id"]
    first_request_id = str(uuid4())
    first = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "first-partial-return-request"},
        ),
        json={
            "return_request_id": first_request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [{"delivery_line_id": delivery_line_id, "quantity_base": "1.000000"}],
        },
    )
    assert first.status_code == 201, first.text

    self_approval = await confirmation_client.post(
        f"/v1/return-requests/{first_request_id}/authorization",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "self-authorize-return"},
        ),
        json={"expected_request_version": 1},
    )
    assert self_approval.status_code == 403, self_approval.text
    assert self_approval.json()["error"]["code"] == "maker_checker_violation"

    under_limit = await confirmation_client.post(
        f"/v1/return-requests/{first_request_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-low-mnl",
            **{"Idempotency-Key": "under-limit-return-authorization"},
        ),
        json={"expected_request_version": 1},
    )
    assert under_limit.status_code == 403, under_limit.text
    assert under_limit.json()["error"]["code"] == "approval_authority_required"

    authorization_headers = auth(
        confirmation_settings,
        "delivery-correction-checker-mnl",
        **{"Idempotency-Key": "authorize-first-partial-return"},
    )
    authorized = await confirmation_client.post(
        f"/v1/return-requests/{first_request_id}/authorization",
        headers=authorization_headers,
        json={"expected_request_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    posted = authorized.json()
    assert posted["status"] == "authorized"
    assert posted["version"] == 2
    assert posted["authorized_by"] == "delivery-correction-checker-mnl"
    assert posted["authorized_at"]
    assert Decimal(posted["lines"][0]["eligible_quantity_base"]) == Decimal("1")

    replay = await confirmation_client.post(
        f"/v1/return-requests/{first_request_id}/authorization",
        headers=authorization_headers,
        json={"expected_request_version": 1},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == posted

    second_request_id = str(uuid4())
    second = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "excess-second-return-request"},
        ),
        json={
            "return_request_id": second_request_id,
            "reason_code": "PRODUCT_DEFECT",
            "reason_label": "Product defect",
            "responsible_party_code": "SUPPLIER",
            "responsible_party_label": "Supplier",
            "lines": [{"delivery_line_id": delivery_line_id, "quantity_base": "1.500000"}],
        },
    )
    assert second.status_code == 201, second.text

    excess = await confirmation_client.post(
        f"/v1/return-requests/{second_request_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "reject-excess-second-return"},
        ),
        json={"expected_request_version": 1},
    )
    assert excess.status_code == 409, excess.text
    assert excess.json()["error"]["code"] == "return_quantity_exceeds_eligible"


@pytest.mark.asyncio
async def test_sealed_return_request_is_immutable_at_the_database_boundary(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    await _grant_return_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())
    created = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "immutable-return-request"},
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

    engine = create_async_engine(postgres_url)
    with pytest.raises(DBAPIError, match="sealed Return Request is immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE return_requests SET reason_label = 'Changed' "
                    "WHERE return_request_id = :request_id"
                ),
                {"request_id": request_id},
            )
    with pytest.raises(DBAPIError, match="sealed Return Request lines are immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE return_request_lines SET quantity_base = 0.500000 "
                    "WHERE return_request_id = :request_id"
                ),
                {"request_id": request_id},
            )
    authorized = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "immutable-return-authorization"},
        ),
        json={"expected_request_version": 1},
    )
    assert authorized.status_code == 200, authorized.text
    with pytest.raises(DBAPIError, match="Return Authorization is immutable"):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM return_authorizations WHERE return_request_id = :request_id"),
                {"request_id": request_id},
            )
    forged_request_id = uuid4()
    with pytest.raises(DBAPIError, match="Return Request source ownership is invalid"):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO return_requests(
                      return_request_id,delivery_receipt_id,confirmation_id,delivery_id,
                      branch_id,warehouse_id,reason_code,reason_label,
                      responsible_party_code,responsible_party_label,requested_by,
                      base_currency,affected_value_base_currency,correlation_id,idempotency_key
                    )
                    SELECT :forged_id,delivery_receipt_id,confirmation_id,delivery_id,
                           branch_id,warehouse_id,reason_code,reason_label,
                           responsible_party_code,responsible_party_label,requested_by,
                           base_currency,0,'forged-value','forged-value'
                    FROM return_requests WHERE return_request_id = :request_id
                    """
                ),
                {"forged_id": forged_request_id, "request_id": request_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO return_request_lines(
                      return_request_line_id,return_request_id,delivery_line_id,line_id,sku_id,
                      quantity_base,delivered_quantity_base,affected_value_base_currency
                    )
                    SELECT :forged_line_id,:forged_id,delivery_line_id,line_id,sku_id,
                           quantity_base,delivered_quantity_base,0
                    FROM return_request_lines WHERE return_request_id = :request_id
                    """
                ),
                {
                    "forged_id": forged_request_id,
                    "forged_line_id": uuid4(),
                    "request_id": request_id,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE return_requests SET sealed_at = now()
                    WHERE return_request_id = :forged_id
                    """
                ),
                {
                    "forged_id": forged_request_id,
                },
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_final_quantity_and_later_delivery_correction_conflict(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    await _grant_return_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    delivery_line_id = confirmation["lines"][0]["delivery_line_id"]

    request_ids = [str(uuid4()), str(uuid4())]
    for index, request_id in enumerate(request_ids):
        created = await confirmation_client.post(
            f"/v1/delivery-receipts/{receipt_id}/return-requests",
            headers=auth(
                confirmation_settings,
                "warehouse-supervisor-mnl",
                **{"Idempotency-Key": f"concurrent-final-request-{index}"},
            ),
            json={
                "return_request_id": request_id,
                "reason_code": "PRODUCT_DEFECT",
                "reason_label": "Product defect",
                "responsible_party_code": "SUPPLIER",
                "responsible_party_label": "Supplier",
                "lines": [{"delivery_line_id": delivery_line_id, "quantity_base": "2.000000"}],
            },
        )
        assert created.status_code == 201, created.text

    results = await asyncio.gather(
        *[
            confirmation_client.post(
                f"/v1/return-requests/{request_id}/authorization",
                headers=auth(
                    confirmation_settings,
                    "delivery-correction-checker-mnl",
                    **{"Idempotency-Key": f"authorize-concurrent-final-{index}"},
                ),
                json={"expected_request_version": 1},
            )
            for index, request_id in enumerate(request_ids)
        ]
    )
    assert sorted(response.status_code for response in results) == [200, 409]
    conflict = next(response for response in results if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "return_quantity_exceeds_eligible"

    correction = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "correction-after-return-authorization"},
        ),
        json={
            "correction_id": str(uuid4()),
            "reason": "Attempted correction after the customer return was authorized.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": delivery_line_id,
                    "accepted_quantity_base": "1.000000",
                    "refused_quantity_base": "1.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "0.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [],
                }
            ],
        },
    )
    assert correction.status_code == 409, correction.text
    assert correction.json()["error"]["code"] == "delivery_correction_not_eligible"


@pytest.mark.asyncio
async def test_return_request_uses_current_corrected_receipt_quantity(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    fake_storage: FakeObjectStorage,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client,
        confirmation_settings,
        postgres_url,
    )
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert await poll_delivery_confirmation_outbox(
        {"database_session_factory": factory, "object_storage": fake_storage}
    ) == {"completed": 1, "failed": 0}
    correction_id = str(uuid4())
    correction = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/corrections",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "correct-before-return-request"},
        ),
        json={
            "correction_id": correction_id,
            "reason": "Only one unit was accepted by the customer.",
            "evidence_ids": [confirmation["evidence_id"]],
            "lines": [
                {
                    "delivery_line_id": confirmation["lines"][0]["delivery_line_id"],
                    "accepted_quantity_base": "1.000000",
                    "refused_quantity_base": "1.000000",
                    "damaged_quantity_base": "0.000000",
                    "short_missing_quantity_base": "0.000000",
                    "still_undelivered_quantity_base": "0.000000",
                    "identity_positions": [],
                }
            ],
        },
    )
    assert correction.status_code == 201, correction.text
    posted = await confirmation_client.post(
        f"/v1/delivery-corrections/{correction_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "authorize-correction-before-return"},
        ),
        json={"expected_correction_version": 1},
    )
    assert posted.status_code == 200, posted.text
    replacement_receipt_id = posted.json()["receipt_effect"]["replacement_delivery_receipt_id"]
    await _grant_return_capabilities(postgres_url)

    request = await confirmation_client.post(
        f"/v1/delivery-receipts/{replacement_receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "return-current-corrected-receipt"},
        ),
        json={
            "return_request_id": str(uuid4()),
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
    assert request.status_code == 201, request.text
    assert Decimal(request.json()["lines"][0]["delivered_quantity_base"]) == Decimal("1")
    stale = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=auth(
            confirmation_settings,
            "warehouse-supervisor-mnl",
            **{"Idempotency-Key": "reject-stale-original-receipt"},
        ),
        json={
            "return_request_id": str(uuid4()),
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
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "return_request_receipt_conflict"
    await engine.dispose()


@pytest.mark.asyncio
async def test_return_authorization_denial_replay_and_rollback_matrix(
    confirmation_client: AsyncClient,
    confirmation_settings: Settings,
    postgres_url: str,
) -> None:
    _, confirmation = await _confirm_fully_accepted_delivery(
        confirmation_client, confirmation_settings, postgres_url
    )
    await _grant_return_capabilities(postgres_url)
    receipt_id = confirmation["delivery_receipt"]["delivery_receipt_id"]
    request_id = str(uuid4())
    command = {
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
    }
    creation_headers = auth(
        confirmation_settings,
        "warehouse-supervisor-mnl",
        **{"Idempotency-Key": "return-request-matrix-replay"},
    )
    created = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=creation_headers,
        json=command,
    )
    assert created.status_code == 201, created.text
    replay = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=creation_headers,
        json=command,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json() == created.json()

    mismatched = await confirmation_client.post(
        f"/v1/delivery-receipts/{receipt_id}/return-requests",
        headers=creation_headers,
        json={**command, "notes": "A different command payload."},
    )
    assert mismatched.status_code == 409, mismatched.text
    assert mismatched.json()["error"]["code"] == "idempotency_conflict"

    attempts = [
        ("ops-admin", "missing-capability", "capability_required"),
        (
            "delivery-correction-checker-ceb",
            "cross-scope",
            "operational_scope_required",
        ),
    ]
    for subject, key, expected_code in attempts:
        denied = await confirmation_client.post(
            f"/v1/return-requests/{request_id}/authorization",
            headers=auth(
                confirmation_settings,
                subject,
                **{"Idempotency-Key": f"return-matrix-{key}"},
            ),
            json={"expected_request_version": 1},
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"]["code"] == expected_code

    stale = await confirmation_client.post(
        f"/v1/return-requests/{request_id}/authorization",
        headers=auth(
            confirmation_settings,
            "delivery-correction-checker-mnl",
            **{"Idempotency-Key": "return-matrix-stale-version"},
        ),
        json={"expected_request_version": 2},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "return_request_version_conflict"

    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        before = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM return_authorizations) AS authorizations,
                          (SELECT count(*) FROM platform_command_receipts) AS command_receipts
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE FUNCTION fail_late_return_authorization()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  RAISE EXCEPTION 'injected late Return Authorization failure';
                END
                $$
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TRIGGER fail_late_return_authorization
                BEFORE INSERT ON return_authorizations
                FOR EACH ROW EXECUTE FUNCTION fail_late_return_authorization()
                """
            )
        )
    try:
        with pytest.raises(DBAPIError, match="injected late Return Authorization failure"):
            await confirmation_client.post(
                f"/v1/return-requests/{request_id}/authorization",
                headers=auth(
                    confirmation_settings,
                    "delivery-correction-checker-mnl",
                    **{"Idempotency-Key": "return-matrix-late-failure"},
                ),
                json={"expected_request_version": 1},
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DROP TRIGGER IF EXISTS fail_late_return_authorization ON return_authorizations"
                )
            )
            await connection.execute(
                text("DROP FUNCTION IF EXISTS fail_late_return_authorization()")
            )
    async with engine.connect() as connection:
        after = dict(
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM return_authorizations) AS authorizations,
                          (SELECT count(*) FROM platform_command_receipts) AS command_receipts
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()
    assert after == before
