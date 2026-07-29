from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TRADEFLOW_REAL_STACK") != "1",
    reason="Runs only against the migrated real-stack acceptance environment.",
)


def auth(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **headers}


def test_external_organization_and_customer_contract() -> None:
    base_url = os.environ.get("TRADEFLOW_REAL_STACK_API_URL", "http://127.0.0.1:8000")
    bootstrap_token = os.environ["TRADEFLOW_REAL_STACK_BOOTSTRAP_TOKEN"]
    sales_token = os.environ["TRADEFLOW_REAL_STACK_SALES_TOKEN"]
    ceb_token = os.environ["TRADEFLOW_REAL_STACK_CEB_TOKEN"]
    admin_token = os.environ["TRADEFLOW_REAL_STACK_ADMIN_TOKEN"]
    credit_token = os.environ["TRADEFLOW_REAL_STACK_CREDIT_TOKEN"]

    bootstrap = {
        "company": {"base_currency": "PHP", "code": "TF", "name": "TradeFlow Distribution"},
        "branches": [
            {
                "code": "MNL",
                "name": "Manila",
                "warehouses": [{"code": "MNL-01", "name": "Manila DC"}],
            },
            {"code": "CEB", "name": "Cebu", "warehouses": [{"code": "CEB-01", "name": "Cebu DC"}]},
        ],
        "role_templates": [
            {
                "capabilities": ["organization:admin"],
                "code": "OPS_ADMIN",
                "name": "Operations admin",
            },
            {
                "capabilities": [
                    "customer:read",
                    "customer:write",
                    "sales:order-read",
                    "sales:order-write",
                    "sales:payment-timing-override",
                    "sales:price-override",
                    "sales:pricing-write",
                ],
                "code": "SALES",
                "name": "Sales",
            },
            {
                "capabilities": [
                    "catalog:write",
                    "inventory:post",
                    "inventory:read",
                    "inventory:rebuild",
                ],
                "code": "INVENTORY",
                "name": "Inventory Controller",
            },
            {
                "capabilities": ["customer:credit-approve"],
                "code": "CREDIT_APPROVER",
                "name": "Credit Approver",
            },
        ],
        "users": [
            {
                "branch_codes": ["MNL", "CEB"],
                "display_name": "Operations Admin",
                "is_operations_administrator": True,
                "role_template_codes": ["OPS_ADMIN"],
                "subject": "operations-admin",
            },
            {
                "branch_codes": ["MNL"],
                "display_name": "Manila Sales",
                "role_template_codes": ["SALES", "CREDIT_APPROVER", "INVENTORY"],
                "subject": "sales-mnl",
                "warehouse_codes": ["MNL-01"],
                "approval_authorities": [
                    {
                        "branch_code": "MNL",
                        "capability": "customer:credit-approve",
                        "maker_checker_required": True,
                        "maximum_amount": "50000.00",
                        "maximum_percentage": None,
                    }
                ],
            },
            {
                "branch_codes": ["CEB"],
                "display_name": "Cebu Sales",
                "role_template_codes": ["SALES"],
                "subject": "sales-ceb",
            },
            {
                "approval_authorities": [
                    {
                        "branch_code": "MNL",
                        "capability": "customer:credit-approve",
                        "maker_checker_required": True,
                        "maximum_amount": "50000.00",
                        "maximum_percentage": None,
                    }
                ],
                "branch_codes": ["MNL"],
                "display_name": "Credit Manager",
                "role_template_codes": ["CREDIT_APPROVER"],
                "subject": "credit-manager",
            },
        ],
    }

    with httpx.Client(base_url=base_url, timeout=10) as client:
        configured = client.post(
            "/v1/organization/bootstrap",
            headers=auth(bootstrap_token, **{"Idempotency-Key": "real-stack-bootstrap"}),
            json=bootstrap,
        )
        assert configured.status_code == 201
        branches = {branch["code"]: branch["branch_id"] for branch in configured.json()["branches"]}

        scope = client.get("/v1/organization/scope", headers=auth(sales_token))
        assert scope.status_code == 200
        assert [branch["code"] for branch in scope.json()["branches"]] == ["MNL"]

        command = {
            "account_number": "MNL-REAL-001",
            "addresses": [
                {
                    "address_key": "BILLING",
                    "city": "Manila",
                    "country_code": "PH",
                    "kind": "billing",
                    "line_1": "100 Real Stack Street",
                    "line_2": None,
                    "postal_code": "1000",
                    "region": "NCR",
                },
                {
                    "address_key": "DELIVERY",
                    "city": "Manila",
                    "country_code": "PH",
                    "kind": "delivery",
                    "line_1": "200 Delivery Avenue",
                    "line_2": None,
                    "postal_code": "1001",
                    "region": "NCR",
                },
            ],
            "branch_id": branches["MNL"],
            "contacts": [
                {
                    "email": "buyer@example.test",
                    "name": "Buyer",
                    "phone": None,
                    "role": "Purchasing",
                }
            ],
            "credit_hold": True,
            "credit_limit": "25000.00",
            "legal_name": "Real Stack Retail",
            "payment_terms": "NET_30",
            "payment_timing_policy": "on_account",
            "status": "active",
        }
        key = f"real-stack-customer-{uuid4()}"
        created = client.post(
            "/v1/customers",
            headers=auth(sales_token, **{"Idempotency-Key": key}),
            json=command,
        )
        replay = client.post(
            "/v1/customers",
            headers=auth(sales_token, **{"Idempotency-Key": key}),
            json=command,
        )
        assert created.status_code == 201
        assert replay.status_code == 200
        assert created.json() == replay.json()

        mnl_search = client.get("/v1/customers?query=Real", headers=auth(sales_token))
        ceb_search = client.get("/v1/customers?query=Real", headers=auth(ceb_token))
        assert [item["account_number"] for item in mnl_search.json()["items"]] == ["MNL-REAL-001"]
        assert ceb_search.json()["items"] == []

        duplicate = client.post(
            "/v1/customers",
            headers=auth(
                sales_token,
                **{"Idempotency-Key": f"duplicate-customer-{uuid4()}"},
            ),
            json=command,
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "customer_account_number_exists"

        maker_approval = client.post(
            f"/v1/customers/{created.json()['customer_id']}/credit-approvals",
            headers=auth(
                sales_token,
                **{"Idempotency-Key": f"maker-credit-{uuid4()}", "If-Match": "1"},
            ),
            json={"reason": "Maker must not approve"},
        )
        assert maker_approval.status_code == 409
        assert maker_approval.json()["error"]["code"] == "maker_checker_violation"

        address_update = client.put(
            f"/v1/customers/{created.json()['customer_id']}/addresses/DELIVERY",
            headers=auth(
                sales_token,
                **{"Idempotency-Key": f"address-update-{uuid4()}", "If-Match": "1"},
            ),
            json={
                "city": "Pasig",
                "country_code": "PH",
                "kind": "delivery",
                "line_1": "300 Updated Delivery Avenue",
                "line_2": None,
                "postal_code": "1600",
                "region": "NCR",
            },
        )
        assert address_update.status_code == 200
        assert address_update.json()["address"]["version"] == 2
        historical = client.get(
            (f"/v1/customers/{created.json()['customer_id']}/addresses/DELIVERY/versions/1"),
            headers=auth(sales_token),
        )
        assert historical.status_code == 200
        assert historical.json()["line_1"] == "200 Delivery Avenue"
        stale_address = client.put(
            f"/v1/customers/{created.json()['customer_id']}/addresses/DELIVERY",
            headers=auth(
                sales_token,
                **{"Idempotency-Key": f"stale-address-{uuid4()}", "If-Match": "1"},
            ),
            json={
                "city": "Pasig",
                "country_code": "PH",
                "kind": "delivery",
                "line_1": "Stale update",
                "line_2": None,
                "postal_code": "1600",
                "region": "NCR",
            },
        )
        assert stale_address.status_code == 409
        assert stale_address.json()["error"]["code"] == "optimistic_version_conflict"

        approved = client.post(
            f"/v1/customers/{created.json()['customer_id']}/credit-approvals",
            headers=auth(
                credit_token,
                **{"Idempotency-Key": f"checker-credit-{uuid4()}", "If-Match": "2"},
            ),
            json={"reason": "Independent credit review"},
        )
        assert approved.status_code == 201
        assert approved.json()["approved_by"] == "credit-manager"
        assert approved.json()["credit_hold"] is False

        admin_approval = client.post(
            f"/v1/customers/{created.json()['customer_id']}/credit-approvals",
            headers=auth(
                admin_token,
                **{"Idempotency-Key": f"admin-credit-{uuid4()}", "If-Match": "3"},
            ),
            json={"reason": "Admin escalation check"},
        )
        assert admin_approval.status_code == 403
        assert admin_approval.json()["error"]["code"] == "capability_required"
