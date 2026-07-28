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
            {"capabilities": ["customer:read", "customer:write"], "code": "SALES", "name": "Sales"},
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
                "role_template_codes": ["SALES"],
                "subject": "sales-mnl",
            },
            {
                "branch_codes": ["CEB"],
                "display_name": "Cebu Sales",
                "role_template_codes": ["SALES"],
                "subject": "sales-ceb",
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
            "credit_hold": False,
            "credit_limit": None,
            "legal_name": "Real Stack Retail",
            "payment_terms": "Due before release",
            "payment_timing_policy": "prepaid",
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

        admin_approval = client.post(
            f"/v1/customers/{created.json()['customer_id']}/credit-approvals",
            headers=auth(
                admin_token,
                **{"Idempotency-Key": f"admin-credit-{uuid4()}", "If-Match": "1"},
            ),
            json={"reason": "Admin escalation check"},
        )
        assert admin_approval.status_code == 403
        assert admin_approval.json()["error"]["code"] == "capability_required"
