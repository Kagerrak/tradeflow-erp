from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def organization_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def organization_client(
    organization_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    app = create_app(organization_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http_client:
            yield http_client


def organization_token(
    settings: Settings,
    *,
    subject: str,
    capabilities: list[str],
) -> str:
    return jwt.encode(
        {
            "sub": subject,
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "name": subject.replace("-", " ").title(),
            "capabilities": capabilities,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )


def organization_bootstrap_command() -> dict[str, object]:
    return {
        "company": {
            "code": "ACME",
            "name": "Acme Distribution",
            "base_currency": "PHP",
        },
        "branches": [
            {
                "code": "MNL",
                "name": "Manila",
                "warehouses": [
                    {
                        "code": "MNL-MAIN",
                        "name": "Manila Main",
                    }
                ],
            },
            {
                "code": "CEB",
                "name": "Cebu",
                "warehouses": [
                    {
                        "code": "CEB-MAIN",
                        "name": "Cebu Main",
                    }
                ],
            },
        ],
        "role_templates": [
            {
                "code": "OPS_ADMIN",
                "name": "Operations Administrator",
                "capabilities": ["organization:admin"],
            },
            {
                "code": "SALES_REP",
                "name": "Sales Representative",
                "capabilities": ["customer:read", "customer:write"],
            },
            {
                "code": "CREDIT_MANAGER",
                "name": "Credit Manager",
                "capabilities": ["customer:credit-approve", "customer:read"],
            },
            {
                "code": "SALES_CREDIT_APPROVER",
                "name": "Sales Credit Approver",
                "capabilities": ["customer:credit-approve"],
            },
        ],
        "users": [
            {
                "subject": "operations-admin",
                "display_name": "Operations Admin",
                "is_operations_administrator": True,
                "role_template_codes": ["OPS_ADMIN"],
                "branch_codes": ["MNL", "CEB"],
                "warehouse_codes": ["MNL-MAIN", "CEB-MAIN"],
                "approval_authorities": [],
            },
            {
                "subject": "sales-mnl",
                "display_name": "Manila Sales",
                "is_operations_administrator": False,
                "role_template_codes": ["SALES_REP", "SALES_CREDIT_APPROVER"],
                "branch_codes": ["MNL"],
                "warehouse_codes": [],
                "approval_authorities": [
                    {
                        "capability": "customer:credit-approve",
                        "branch_code": "MNL",
                        "maximum_amount": "50000.00",
                        "maximum_percentage": None,
                        "maker_checker_required": True,
                    }
                ],
            },
            {
                "subject": "sales-ceb",
                "display_name": "Cebu Sales",
                "is_operations_administrator": False,
                "role_template_codes": ["SALES_REP"],
                "branch_codes": ["CEB"],
                "warehouse_codes": [],
                "approval_authorities": [],
            },
            {
                "subject": "credit-manager",
                "display_name": "Credit Manager",
                "is_operations_administrator": False,
                "role_template_codes": ["CREDIT_MANAGER"],
                "branch_codes": ["MNL"],
                "warehouse_codes": [],
                "approval_authorities": [
                    {
                        "capability": "customer:credit-approve",
                        "branch_code": "MNL",
                        "maximum_amount": "50000.00",
                        "maximum_percentage": None,
                        "maker_checker_required": True,
                    }
                ],
            },
        ],
    }


async def bootstrap_organization(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, object]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers={
            "authorization": (
                "Bearer "
                + organization_token(
                    settings,
                    subject="operations-admin",
                    capabilities=["organization:bootstrap"],
                )
            ),
            "idempotency-key": "bootstrap-acme-distribution",
        },
        json=organization_bootstrap_command(),
    )
    assert response.status_code == 201
    return response.json()


def customer_command(
    *,
    account_number: str,
    branch_id: str,
    legal_name: str,
) -> dict[str, object]:
    return {
        "account_number": account_number,
        "branch_id": branch_id,
        "legal_name": legal_name,
        "status": "active",
        "payment_terms": "NET_30",
        "payment_timing_policy": "on_account",
        "credit_limit": "25000.00",
        "credit_hold": True,
        "contacts": [
            {
                "name": "Maria Santos",
                "role": "Purchasing",
                "email": "maria@customer.example",
                "phone": "+63 917 555 0101",
            }
        ],
        "addresses": [
            {
                "address_key": "BILLING",
                "kind": "billing",
                "line_1": "18 Port Road",
                "line_2": None,
                "city": "Manila",
                "region": "NCR",
                "postal_code": "1018",
                "country_code": "PH",
            },
            {
                "address_key": "DELIVERY",
                "kind": "delivery",
                "line_1": "42 Warehouse Avenue",
                "line_2": "Tondo",
                "city": "Manila",
                "region": "NCR",
                "postal_code": "1012",
                "country_code": "PH",
            },
        ],
    }


def user_headers(
    settings: Settings,
    *,
    subject: str,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers = {
        "authorization": (
            "Bearer "
            + organization_token(
                settings,
                subject=subject,
                capabilities=[],
            )
        )
    }
    if idempotency_key is not None:
        headers["idempotency-key"] = idempotency_key
    return headers


async def create_customer_account(
    client: AsyncClient,
    settings: Settings,
    *,
    subject: str,
    branch_id: str,
    account_number: str,
    legal_name: str,
) -> dict[str, object]:
    response = await client.post(
        "/v1/customers",
        headers=user_headers(
            settings,
            subject=subject,
            idempotency_key=f"create-{account_number}",
        ),
        json=customer_command(
            account_number=account_number,
            branch_id=branch_id,
            legal_name=legal_name,
        ),
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_bootstrap_configures_exactly_one_company_and_replays_safely(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    headers = {
        "authorization": (
            "Bearer "
            + organization_token(
                organization_settings,
                subject="operations-admin",
                capabilities=["organization:bootstrap"],
            )
        ),
        "idempotency-key": "bootstrap-acme-distribution",
    }
    command = organization_bootstrap_command()

    first = await organization_client.post(
        "/v1/organization/bootstrap",
        headers=headers,
        json=command,
    )
    replay = await organization_client.post(
        "/v1/organization/bootstrap",
        headers=headers,
        json=command,
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["company"] == {
        "base_currency": "PHP",
        "code": "ACME",
        "name": "Acme Distribution",
        "version": 1,
    }
    assert first.json()["branches"][0]["warehouses"][0]["code"] == "MNL-MAIN"
    assert first.headers["x-idempotency-replayed"] == "false"
    assert replay.headers["x-idempotency-replayed"] == "true"


@pytest.mark.asyncio
async def test_scoped_sales_user_creates_a_customer_account_idempotently(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    organization = await bootstrap_organization(
        organization_client,
        organization_settings,
    )
    manila_branch = next(branch for branch in organization["branches"] if branch["code"] == "MNL")
    headers = user_headers(
        organization_settings,
        subject="sales-mnl",
        idempotency_key="create-customer-mnl-001",
    )
    command = customer_command(
        account_number="MNL-0001",
        branch_id=manila_branch["branch_id"],
        legal_name="North Harbor Stores",
    )

    first = await organization_client.post(
        "/v1/customers",
        headers=headers,
        json=command,
    )
    replay = await organization_client.post(
        "/v1/customers",
        headers=headers,
        json=command,
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["account_number"] == "MNL-0001"
    assert first.json()["version"] == 1
    assert first.json()["payment_timing_policy"] == "on_account"
    assert first.json()["credit_limit"] == "25000.00"
    assert first.json()["contacts"][0]["name"] == "Maria Santos"
    assert [address["version"] for address in first.json()["addresses"]] == [1, 1]


@pytest.mark.asyncio
async def test_customer_search_is_isolated_to_persisted_branch_scope(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    organization = await bootstrap_organization(
        organization_client,
        organization_settings,
    )
    branch_ids = {branch["code"]: branch["branch_id"] for branch in organization["branches"]}
    for subject, branch_code, account_number, legal_name in [
        ("sales-mnl", "MNL", "MNL-0001", "North Harbor Stores"),
        ("sales-ceb", "CEB", "CEB-0001", "South Harbor Stores"),
    ]:
        created = await organization_client.post(
            "/v1/customers",
            headers=user_headers(
                organization_settings,
                subject=subject,
                idempotency_key=f"create-{account_number}",
            ),
            json=customer_command(
                account_number=account_number,
                branch_id=branch_ids[branch_code],
                legal_name=legal_name,
            ),
        )
        assert created.status_code == 201

    response = await organization_client.get(
        "/v1/customers",
        headers=user_headers(organization_settings, subject="sales-mnl"),
        params={"query": "Harbor Stores"},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["account_number"] for item in response.json()["items"]] == ["MNL-0001"]


@pytest.mark.asyncio
async def test_address_update_preserves_history_and_rejects_a_stale_version(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    organization = await bootstrap_organization(
        organization_client,
        organization_settings,
    )
    manila_branch = next(branch for branch in organization["branches"] if branch["code"] == "MNL")
    customer = await create_customer_account(
        organization_client,
        organization_settings,
        subject="sales-mnl",
        branch_id=manila_branch["branch_id"],
        account_number="MNL-0001",
        legal_name="North Harbor Stores",
    )
    update_command = {
        "kind": "delivery",
        "line_1": "88 New Warehouse Avenue",
        "line_2": "Tondo",
        "city": "Manila",
        "region": "NCR",
        "postal_code": "1012",
        "country_code": "PH",
    }
    updated = await organization_client.put(
        f"/v1/customers/{customer['customer_id']}/addresses/DELIVERY",
        headers={
            **user_headers(
                organization_settings,
                subject="sales-mnl",
                idempotency_key="update-mnl-0001-delivery-v2",
            ),
            "if-match": "1",
        },
        json=update_command,
    )
    historical = await organization_client.get(
        (f"/v1/customers/{customer['customer_id']}/addresses/DELIVERY/versions/1"),
        headers=user_headers(organization_settings, subject="sales-mnl"),
    )
    stale = await organization_client.put(
        f"/v1/customers/{customer['customer_id']}/addresses/DELIVERY",
        headers={
            **user_headers(
                organization_settings,
                subject="sales-mnl",
                idempotency_key="stale-update-mnl-0001-delivery",
            ),
            "if-match": "1",
        },
        json=update_command,
    )

    assert updated.status_code == 200
    assert updated.json()["customer_version"] == 2
    assert updated.json()["address"]["version"] == 2
    assert updated.json()["address"]["line_1"] == "88 New Warehouse Avenue"
    assert historical.status_code == 200
    assert historical.json()["version"] == 1
    assert historical.json()["line_1"] == "42 Warehouse Avenue"
    assert historical.json()["is_current"] is False
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "optimistic_version_conflict"


@pytest.mark.asyncio
async def test_credit_approval_enforces_authority_maker_checker_and_admin_separation(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    organization = await bootstrap_organization(
        organization_client,
        organization_settings,
    )
    manila_branch = next(branch for branch in organization["branches"] if branch["code"] == "MNL")
    customer = await create_customer_account(
        organization_client,
        organization_settings,
        subject="sales-mnl",
        branch_id=manila_branch["branch_id"],
        account_number="MNL-0001",
        legal_name="North Harbor Stores",
    )
    endpoint = f"/v1/customers/{customer['customer_id']}/credit-approvals"
    command = {"reason": "Approved trade reference and payment history."}

    administrator = await organization_client.post(
        endpoint,
        headers={
            **user_headers(
                organization_settings,
                subject="operations-admin",
                idempotency_key="admin-credit-attempt",
            ),
            "if-match": "1",
        },
        json=command,
    )
    maker = await organization_client.post(
        endpoint,
        headers={
            **user_headers(
                organization_settings,
                subject="sales-mnl",
                idempotency_key="maker-credit-attempt",
            ),
            "if-match": "1",
        },
        json=command,
    )
    checker = await organization_client.post(
        endpoint,
        headers={
            **user_headers(
                organization_settings,
                subject="credit-manager",
                idempotency_key="checker-credit-approval",
            ),
            "if-match": "1",
        },
        json=command,
    )

    assert administrator.status_code == 403
    assert administrator.json()["error"]["code"] == "capability_required"
    assert maker.status_code == 409
    assert maker.json()["error"]["code"] == "maker_checker_violation"
    assert checker.status_code == 201
    assert checker.json()["approved_by"] == "credit-manager"
    assert checker.json()["customer_version"] == 2
    assert checker.json()["credit_limit"] == "25000.00"
    assert checker.json()["credit_hold"] is False


@pytest.mark.asyncio
async def test_base_currency_is_immutable_and_locations_keep_lifecycle_identity(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    organization = await bootstrap_organization(
        organization_client,
        organization_settings,
    )
    manila_branch = next(branch for branch in organization["branches"] if branch["code"] == "MNL")
    manila_warehouse = manila_branch["warehouses"][0]
    admin_headers = user_headers(
        organization_settings,
        subject="operations-admin",
    )

    currency_change = await organization_client.patch(
        "/v1/organization/company",
        headers={
            **admin_headers,
            "idempotency-key": "change-base-currency",
            "if-match": "1",
        },
        json={
            "name": "Acme Distribution",
            "base_currency": "USD",
        },
    )
    branch_inactive = await organization_client.patch(
        f"/v1/organization/branches/{manila_branch['branch_id']}",
        headers={
            **admin_headers,
            "idempotency-key": "deactivate-manila",
            "if-match": "1",
        },
        json={"is_active": False},
    )
    warehouse_inactive = await organization_client.patch(
        f"/v1/organization/warehouses/{manila_warehouse['warehouse_id']}",
        headers={
            **admin_headers,
            "idempotency-key": "deactivate-manila-main",
            "if-match": "1",
        },
        json={"is_active": False},
    )
    branch_active = await organization_client.patch(
        f"/v1/organization/branches/{manila_branch['branch_id']}",
        headers={
            **admin_headers,
            "idempotency-key": "reactivate-manila",
            "if-match": "2",
        },
        json={"is_active": True},
    )

    assert currency_change.status_code == 409
    assert currency_change.json()["error"]["code"] == "base_currency_immutable"
    assert branch_inactive.status_code == 200
    assert branch_inactive.json() == {
        "branch_id": manila_branch["branch_id"],
        "code": "MNL",
        "is_active": False,
        "name": "Manila",
        "version": 2,
    }
    assert warehouse_inactive.status_code == 200
    assert warehouse_inactive.json()["warehouse_id"] == manila_warehouse["warehouse_id"]
    assert warehouse_inactive.json()["is_active"] is False
    assert warehouse_inactive.json()["version"] == 2
    assert branch_active.status_code == 200
    assert branch_active.json()["branch_id"] == manila_branch["branch_id"]
    assert branch_active.json()["is_active"] is True
    assert branch_active.json()["version"] == 3


@pytest.mark.asyncio
async def test_customer_number_uniqueness_and_write_scope_are_enforced(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    organization = await bootstrap_organization(
        organization_client,
        organization_settings,
    )
    branch_ids = {branch["code"]: branch["branch_id"] for branch in organization["branches"]}
    created = await create_customer_account(
        organization_client,
        organization_settings,
        subject="sales-mnl",
        branch_id=branch_ids["MNL"],
        account_number="SHARED-0001",
        legal_name="North Harbor Stores",
    )
    duplicate = await organization_client.post(
        "/v1/customers",
        headers=user_headers(
            organization_settings,
            subject="sales-ceb",
            idempotency_key="duplicate-shared-0001",
        ),
        json=customer_command(
            account_number=created["account_number"],
            branch_id=branch_ids["CEB"],
            legal_name="South Harbor Stores",
        ),
    )
    outside_scope = await organization_client.post(
        "/v1/customers",
        headers=user_headers(
            organization_settings,
            subject="sales-mnl",
            idempotency_key="outside-scope-customer",
        ),
        json=customer_command(
            account_number="CEB-UNAUTHORIZED",
            branch_id=branch_ids["CEB"],
            legal_name="Unauthorized Cebu Account",
        ),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "customer_account_number_exists"
    assert outside_scope.status_code == 403
    assert outside_scope.json()["error"]["code"] == "operational_scope_required"


@pytest.mark.asyncio
async def test_user_scope_exposes_only_persisted_operational_assignments(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    await bootstrap_organization(
        organization_client,
        organization_settings,
    )

    response = await organization_client.get(
        "/v1/organization/scope",
        headers=user_headers(organization_settings, subject="sales-mnl"),
    )

    assert response.status_code == 200
    assert response.json()["user"]["subject"] == "sales-mnl"
    assert response.json()["user"]["is_operations_administrator"] is False
    assert response.json()["capabilities"] == [
        "customer:credit-approve",
        "customer:read",
        "customer:write",
    ]
    assert [branch["code"] for branch in response.json()["branches"]] == ["MNL"]
    assert response.json()["warehouses"] == []


@pytest.mark.asyncio
async def test_operations_admin_configures_role_templates_and_user_assignments(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    await bootstrap_organization(organization_client, organization_settings)
    admin_headers = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="configure-auditor-role",
    )
    admin_headers["If-Match"] = "0"

    role = await organization_client.put(
        "/v1/organization/role-templates/ACCOUNT_VIEWER",
        headers=admin_headers,
        json={
            "name": "Account Viewer",
            "is_active": True,
            "capabilities": ["customer:read"],
        },
    )
    role_replay = await organization_client.put(
        "/v1/organization/role-templates/ACCOUNT_VIEWER",
        headers=admin_headers,
        json={
            "name": "Account Viewer",
            "is_active": True,
            "capabilities": ["customer:read"],
        },
    )

    assert role.status_code == 201
    assert role_replay.status_code == 200
    assert role.json()["version"] == 1
    assert role.json()["capabilities"] == ["customer:read"]

    user_headers_admin = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="configure-auditor-user",
    )
    user_headers_admin["If-Match"] = "0"
    configured = await organization_client.put(
        "/v1/organization/users/auditor",
        headers=user_headers_admin,
        json={
            "display_name": "Customer Auditor",
            "is_active": True,
            "is_operations_administrator": False,
            "role_template_codes": ["ACCOUNT_VIEWER"],
            "branch_codes": ["MNL"],
            "warehouse_codes": [],
            "approval_authorities": [],
        },
    )
    assert configured.status_code == 201
    assert configured.json()["version"] == 1

    scope = await organization_client.get(
        "/v1/organization/scope",
        headers=user_headers(organization_settings, subject="auditor"),
    )
    assert scope.status_code == 200
    assert scope.json()["capabilities"] == ["customer:read"]
    assert [branch["code"] for branch in scope.json()["branches"]] == ["MNL"]

    update_role_headers = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="expand-auditor-role",
    )
    update_role_headers["If-Match"] = "1"
    expanded_role = await organization_client.put(
        "/v1/organization/role-templates/ACCOUNT_VIEWER",
        headers=update_role_headers,
        json={
            "name": "Account Viewer",
            "is_active": True,
            "capabilities": ["customer:read", "customer:write"],
        },
    )
    assert expanded_role.status_code == 200
    assert expanded_role.json()["version"] == 2
    expanded_scope = await organization_client.get(
        "/v1/organization/scope",
        headers=user_headers(organization_settings, subject="auditor"),
    )
    assert expanded_scope.json()["capabilities"] == ["customer:read", "customer:write"]

    update_headers = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="move-auditor-scope",
    )
    update_headers["If-Match"] = "1"
    moved = await organization_client.put(
        "/v1/organization/users/auditor",
        headers=update_headers,
        json={
            "display_name": "Customer Auditor",
            "is_active": True,
            "is_operations_administrator": False,
            "role_template_codes": ["ACCOUNT_VIEWER"],
            "branch_codes": ["CEB"],
            "warehouse_codes": [],
            "approval_authorities": [],
        },
    )
    assert moved.status_code == 200
    assert moved.json()["version"] == 2

    moved_scope = await organization_client.get(
        "/v1/organization/scope",
        headers=user_headers(organization_settings, subject="auditor"),
    )
    assert [branch["code"] for branch in moved_scope.json()["branches"]] == ["CEB"]


@pytest.mark.asyncio
async def test_idempotent_replay_revalidates_current_scope_and_authority(
    organization_client: AsyncClient,
    organization_settings: Settings,
) -> None:
    bootstrap = await bootstrap_organization(organization_client, organization_settings)
    branch_ids = {branch["code"]: branch["branch_id"] for branch in bootstrap["branches"]}
    customer = await create_customer_account(
        organization_client,
        organization_settings,
        account_number="MNL-REPLAY-AUTH",
        branch_id=branch_ids["MNL"],
        legal_name="Replay Authorization Account",
        subject="sales-mnl",
    )
    address_command = {
        "kind": "delivery",
        "line_1": "51 Revalidation Road",
        "line_2": None,
        "city": "Manila",
        "region": "NCR",
        "postal_code": "1012",
        "country_code": "PH",
    }
    replay_headers = user_headers(
        organization_settings,
        subject="sales-mnl",
        idempotency_key="replay-address-after-scope-revocation",
    )
    replay_headers["If-Match"] = "1"
    first = await organization_client.put(
        f"/v1/customers/{customer['customer_id']}/addresses/DELIVERY",
        headers=replay_headers,
        json=address_command,
    )
    assert first.status_code == 200

    credit_headers = user_headers(
        organization_settings,
        subject="credit-manager",
        idempotency_key="replay-credit-after-authority-revocation",
    )
    credit_headers["If-Match"] = "2"
    credit = await organization_client.post(
        f"/v1/customers/{customer['customer_id']}/credit-approvals",
        headers=credit_headers,
        json={"reason": "Validated before authority revocation"},
    )
    assert credit.status_code == 201

    revoke_credit_headers = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="revoke-credit-manager-authority",
    )
    revoke_credit_headers["If-Match"] = "1"
    revoked_credit = await organization_client.put(
        "/v1/organization/users/credit-manager",
        headers=revoke_credit_headers,
        json={
            "display_name": "Credit Manager",
            "is_active": True,
            "is_operations_administrator": False,
            "role_template_codes": ["CREDIT_MANAGER"],
            "branch_codes": ["MNL"],
            "warehouse_codes": [],
            "approval_authorities": [],
        },
    )
    assert revoked_credit.status_code == 200
    credit_replay = await organization_client.post(
        f"/v1/customers/{customer['customer_id']}/credit-approvals",
        headers=credit_headers,
        json={"reason": "Validated before authority revocation"},
    )
    assert credit_replay.status_code == 403
    assert credit_replay.json()["error"]["code"] == "approval_authority_required"

    revoke_headers = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="revoke-sales-mnl-scope",
    )
    revoke_headers["If-Match"] = "1"
    revoked = await organization_client.put(
        "/v1/organization/users/sales-mnl",
        headers=revoke_headers,
        json={
            "display_name": "Manila Sales",
            "is_active": True,
            "is_operations_administrator": False,
            "role_template_codes": ["SALES_REP", "SALES_CREDIT_APPROVER"],
            "branch_codes": [],
            "warehouse_codes": [],
            "approval_authorities": [],
        },
    )
    assert revoked.status_code == 200

    replay = await organization_client.put(
        f"/v1/customers/{customer['customer_id']}/addresses/DELIVERY",
        headers=replay_headers,
        json=address_command,
    )
    assert replay.status_code == 403
    assert replay.json()["error"]["code"] == "operational_scope_required"

    warehouse_ids = {
        warehouse["code"]: warehouse["warehouse_id"]
        for branch in bootstrap["branches"]
        for warehouse in branch["warehouses"]
    }
    branch_headers = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="replay-branch-after-scope-revocation",
    )
    branch_headers["If-Match"] = "1"
    warehouse_headers = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="replay-warehouse-after-scope-revocation",
    )
    warehouse_headers["If-Match"] = "1"
    assert (
        await organization_client.patch(
            f"/v1/organization/branches/{branch_ids['CEB']}",
            headers=branch_headers,
            json={"is_active": False},
        )
    ).status_code == 200
    assert (
        await organization_client.patch(
            f"/v1/organization/warehouses/{warehouse_ids['MNL-MAIN']}",
            headers=warehouse_headers,
            json={"is_active": False},
        )
    ).status_code == 200

    revoke_admin_headers = user_headers(
        organization_settings,
        subject="operations-admin",
        idempotency_key="revoke-admin-operational-scope",
    )
    revoke_admin_headers["If-Match"] = "1"
    assert (
        await organization_client.put(
            "/v1/organization/users/operations-admin",
            headers=revoke_admin_headers,
            json={
                "display_name": "Operations Admin",
                "is_active": True,
                "is_operations_administrator": True,
                "role_template_codes": ["OPS_ADMIN"],
                "branch_codes": [],
                "warehouse_codes": [],
                "approval_authorities": [],
            },
        )
    ).status_code == 200
    branch_replay = await organization_client.patch(
        f"/v1/organization/branches/{branch_ids['CEB']}",
        headers=branch_headers,
        json={"is_active": False},
    )
    warehouse_replay = await organization_client.patch(
        f"/v1/organization/warehouses/{warehouse_ids['MNL-MAIN']}",
        headers=warehouse_headers,
        json={"is_active": False},
    )
    assert branch_replay.status_code == 403
    assert warehouse_replay.status_code == 403
