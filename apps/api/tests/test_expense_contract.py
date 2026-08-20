from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def expense_settings(postgres_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=postgres_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def expense_client(expense_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(expense_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http_client:
            yield http_client


def _token(
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
        cast(str, settings.auth_test_secret),
        algorithm="HS256",
    )


def _headers(
    settings: Settings,
    *,
    subject: str,
    capabilities: list[str],
    idempotency_key: str,
) -> dict[str, str]:
    return {
        "authorization": "Bearer " + _token(settings, subject=subject, capabilities=capabilities),
        "idempotency-key": idempotency_key,
    }


async def _bootstrap_organization(
    client: AsyncClient,
    settings: Settings,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/organization/bootstrap",
        headers=_headers(
            settings,
            subject="operations-admin",
            capabilities=["organization:bootstrap"],
            idempotency_key="expense-bootstrap",
        ),
        json={
            "company": {
                "code": "ACME",
                "name": "Acme Distribution",
                "base_currency": "PHP",
            },
            "branches": [
                {
                    "code": "MNL",
                    "name": "Manila",
                    "warehouses": [{"code": "MNL-MAIN", "name": "Manila Main"}],
                },
                {
                    "code": "CEB",
                    "name": "Cebu",
                    "warehouses": [{"code": "CEB-MAIN", "name": "Cebu Main"}],
                },
            ],
            "role_templates": [
                {
                    "code": "EXPENSE_CREATOR",
                    "name": "Expense Creator",
                    "capabilities": [
                        "finance:expense-category-create",
                        "finance:expense-policy-create",
                    ],
                },
                {
                    "code": "EXPENSE_PUBLISHER",
                    "name": "Expense Publisher",
                    "capabilities": [
                        "finance:expense-category-read",
                        "finance:expense-policy-read",
                        "finance:expense-category-publish",
                        "finance:expense-policy-publish",
                    ],
                },
                {
                    "code": "EXPENSE_READER",
                    "name": "Expense Reader",
                    "capabilities": [
                        "finance:expense-category-read",
                        "finance:expense-policy-read",
                    ],
                },
            ],
            "users": [
                {
                    "subject": "category-creator",
                    "display_name": "Category Creator",
                    "is_operations_administrator": False,
                    "role_template_codes": ["EXPENSE_CREATOR"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [],
                },
                {
                    "subject": "category-publisher",
                    "display_name": "Category Publisher",
                    "is_operations_administrator": False,
                    "role_template_codes": ["EXPENSE_PUBLISHER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [
                        {
                            "capability": "finance:expense-category-publish",
                            "branch_code": "MNL",
                            "maximum_amount": None,
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        }
                    ],
                },
                {
                    "subject": "category-publisher-limited",
                    "display_name": "Limited Category Publisher",
                    "is_operations_administrator": False,
                    "role_template_codes": ["EXPENSE_PUBLISHER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [
                        {
                            "capability": "finance:expense-category-publish",
                            "branch_code": "MNL",
                            "maximum_amount": "500.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        }
                    ],
                },
                {
                    "subject": "expense-admin",
                    "display_name": "Expense Admin",
                    "is_operations_administrator": False,
                    "role_template_codes": ["EXPENSE_CREATOR", "EXPENSE_PUBLISHER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [
                        {
                            "capability": "finance:expense-category-publish",
                            "branch_code": "MNL",
                            "maximum_amount": None,
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        },
                        {
                            "capability": "finance:expense-policy-publish",
                            "branch_code": "MNL",
                            "maximum_amount": "2000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        },
                    ],
                },
                {
                    "subject": "policy-creator",
                    "display_name": "Policy Creator",
                    "is_operations_administrator": False,
                    "role_template_codes": ["EXPENSE_CREATOR"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [],
                },
                {
                    "subject": "policy-publisher",
                    "display_name": "Policy Publisher",
                    "is_operations_administrator": False,
                    "role_template_codes": ["EXPENSE_PUBLISHER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [
                        {
                            "capability": "finance:expense-policy-publish",
                            "branch_code": "MNL",
                            "maximum_amount": "1000.00",
                            "maximum_percentage": None,
                            "maker_checker_required": True,
                        }
                    ],
                },
                {
                    "subject": "expense-reader",
                    "display_name": "Expense Reader",
                    "is_operations_administrator": False,
                    "role_template_codes": ["EXPENSE_READER"],
                    "branch_codes": ["MNL"],
                    "warehouse_codes": [],
                    "approval_authorities": [],
                },
            ],
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


async def _create_category(
    client: AsyncClient,
    settings: Settings,
    *,
    code: str,
    effective_from: str,
    effective_to: str | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/finance/expense-categories",
        headers=_headers(
            settings,
            subject="category-creator",
            capabilities=["finance:expense-category-create"],
            idempotency_key=idempotency_key,
        ),
        json={
            "category_code": code,
            "name": code.replace("_", " ").title(),
            "description": f"Description for {code}",
            "allowed_evidence_types": ["receipt", "invoice"],
            "attribution_rules": {
                "cost_center_required": True,
                "branch_required": False,
                "project_allowed": True,
                "supplier_allowed": False,
                "employee_allowed": True,
            },
            "effective_from": effective_from,
            "effective_to": effective_to,
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


async def _publish_category(
    client: AsyncClient,
    settings: Settings,
    *,
    code: str,
    version: int,
    publisher: str,
    idempotency_key: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/finance/expense-categories/{code}/versions/{version}/publish",
        headers=_headers(
            settings,
            subject=publisher,
            capabilities=[
                "finance:expense-category-publish",
                "finance:expense-category-read",
            ],
            idempotency_key=idempotency_key,
        ),
        json={},
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


async def _create_policy(
    client: AsyncClient,
    settings: Settings,
    *,
    code: str,
    category_version_id: str,
    effective_from: str,
    effective_to: str | None = None,
    max_amount: str | None = "500.00",
    idempotency_key: str,
    branch_id: str | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/finance/expense-policies",
        headers=_headers(
            settings,
            subject="policy-creator",
            capabilities=["finance:expense-policy-create"],
            idempotency_key=idempotency_key,
        ),
        json={
            "policy_code": code,
            "name": code.replace("_", " ").title(),
            "description": f"Description for {code}",
            "branch_id": branch_id,
            "category_version_id": category_version_id,
            "max_amount": max_amount,
            "currencies": ["PHP"],
            "requires_receipt": True,
            "allowed_evidence_types": ["receipt"],
            "attribution_rules": {
                "cost_center_required": True,
                "branch_required": True,
                "project_allowed": False,
                "supplier_allowed": False,
                "employee_allowed": True,
            },
            "effective_from": effective_from,
            "effective_to": effective_to,
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


async def _publish_policy(
    client: AsyncClient,
    settings: Settings,
    *,
    code: str,
    version: int,
    publisher: str,
    idempotency_key: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/finance/expense-policies/{code}/versions/{version}/publish",
        headers=_headers(
            settings,
            subject=publisher,
            capabilities=[
                "finance:expense-policy-publish",
                "finance:expense-policy-read",
            ],
            idempotency_key=idempotency_key,
        ),
        json={},
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


async def test_create_and_publish_category_happy_path(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)

    created = await _create_category(
        expense_client,
        expense_settings,
        code="TRAVEL",
        effective_from="2026-01-01",
        idempotency_key="create-travel",
    )
    assert created["status"] == "draft"
    assert created["version"] == 1

    published = await _publish_category(
        expense_client,
        expense_settings,
        code="TRAVEL",
        version=1,
        publisher="category-publisher",
        idempotency_key="publish-travel",
    )
    assert published["status"] == "published"
    assert published["published_by"] == "category-publisher"

    listed = await expense_client.get(
        "/v1/finance/expense-categories?status=published",
        headers=_headers(
            expense_settings,
            subject="expense-reader",
            capabilities=["finance:expense-category-read"],
            idempotency_key="list-categories",
        ),
    )
    assert listed.status_code == 200
    data = listed.json()
    assert len(data) == 1
    assert data[0]["category_code"] == "TRAVEL"

    fetched = await expense_client.get(
        "/v1/finance/expense-categories/TRAVEL",
        headers=_headers(
            expense_settings,
            subject="expense-reader",
            capabilities=["finance:expense-category-read"],
            idempotency_key="get-travel",
        ),
    )
    assert fetched.status_code == 200
    assert fetched.json()[0]["status"] == "published"


async def test_create_and_publish_policy_happy_path(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    category = await _create_category(
        expense_client,
        expense_settings,
        code="MEALS",
        effective_from="2026-01-01",
        idempotency_key="create-meals",
    )
    await _publish_category(
        expense_client,
        expense_settings,
        code="MEALS",
        version=1,
        publisher="category-publisher",
        idempotency_key="publish-meals",
    )

    policy = await _create_policy(
        expense_client,
        expense_settings,
        code="MEALS_POLICY",
        category_version_id=category["expense_category_version_id"],
        effective_from="2026-01-01",
        max_amount="500.00",
        idempotency_key="create-meals-policy",
    )
    assert policy["status"] == "draft"
    assert policy["version"] == 1

    published = await _publish_policy(
        expense_client,
        expense_settings,
        code="MEALS_POLICY",
        version=1,
        publisher="policy-publisher",
        idempotency_key="publish-meals-policy",
    )
    assert published["status"] == "published"
    assert published["published_by"] == "policy-publisher"


async def test_category_publication_requires_capability(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    created = await _create_category(
        expense_client,
        expense_settings,
        code="OFFICE",
        effective_from="2026-01-01",
        idempotency_key="create-office",
    )

    response = await expense_client.post(
        f"/v1/finance/expense-categories/OFFICE/versions/{created['version']}/publish",
        headers=_headers(
            expense_settings,
            subject="category-creator",
            capabilities=["finance:expense-category-create"],
            idempotency_key="publish-office-unauthorized",
        ),
        json={},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_required"


async def test_self_publication_rejected(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    created = await expense_client.post(
        "/v1/finance/expense-categories",
        headers=_headers(
            expense_settings,
            subject="expense-admin",
            capabilities=[
                "finance:expense-category-create",
                "finance:expense-category-publish",
                "finance:expense-category-read",
            ],
            idempotency_key="create-supplies-admin",
        ),
        json={
            "category_code": "SUPPLIES",
            "name": "Supplies",
            "description": "Office supplies",
            "allowed_evidence_types": ["receipt", "invoice"],
            "attribution_rules": {
                "cost_center_required": True,
                "branch_required": False,
                "project_allowed": True,
                "supplier_allowed": False,
                "employee_allowed": True,
            },
            "effective_from": "2026-01-01",
        },
    )
    assert created.status_code == 201

    response = await expense_client.post(
        f"/v1/finance/expense-categories/SUPPLIES/versions/{created.json()['version']}/publish",
        headers=_headers(
            expense_settings,
            subject="expense-admin",
            capabilities=[
                "finance:expense-category-create",
                "finance:expense-category-publish",
                "finance:expense-category-read",
            ],
            idempotency_key="publish-supplies-self",
        ),
        json={},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "self_publication_forbidden"


async def test_stale_publication_rejected(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    created = await _create_category(
        expense_client,
        expense_settings,
        code="TRAINING",
        effective_from="2026-01-01",
        idempotency_key="create-training",
    )
    await _publish_category(
        expense_client,
        expense_settings,
        code="TRAINING",
        version=1,
        publisher="category-publisher",
        idempotency_key="publish-training",
    )

    retry = await expense_client.post(
        f"/v1/finance/expense-categories/TRAINING/versions/{created['version']}/publish",
        headers=_headers(
            expense_settings,
            subject="category-publisher",
            capabilities=[
                "finance:expense-category-publish",
                "finance:expense-category-read",
            ],
            idempotency_key="publish-training-stale",
        ),
        json={},
    )
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "stale_publication"


async def test_overlapping_category_effective_range_rejected(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    await _create_category(
        expense_client,
        expense_settings,
        code="TRANSPORT",
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        idempotency_key="create-transport-1",
    )
    await _publish_category(
        expense_client,
        expense_settings,
        code="TRANSPORT",
        version=1,
        publisher="category-publisher",
        idempotency_key="publish-transport-1",
    )

    overlapping = await _create_category(
        expense_client,
        expense_settings,
        code="TRANSPORT",
        effective_from="2026-06-01",
        effective_to="2026-09-30",
        idempotency_key="create-transport-2",
    )

    response = await expense_client.post(
        f"/v1/finance/expense-categories/TRANSPORT/versions/{overlapping['version']}/publish",
        headers=_headers(
            expense_settings,
            subject="category-publisher",
            capabilities=[
                "finance:expense-category-publish",
                "finance:expense-category-read",
            ],
            idempotency_key="publish-transport-2",
        ),
        json={},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "effective_range_overlap"


async def test_policy_requires_published_category(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    category = await _create_category(
        expense_client,
        expense_settings,
        code="DRAFT_CAT",
        effective_from="2026-01-01",
        idempotency_key="create-draft-cat",
    )

    response = await expense_client.post(
        "/v1/finance/expense-policies",
        headers=_headers(
            expense_settings,
            subject="policy-creator",
            capabilities=["finance:expense-policy-create"],
            idempotency_key="create-policy-draft-cat",
        ),
        json={
            "policy_code": "DRAFT_POLICY",
            "name": "Draft Policy",
            "category_version_id": category["expense_category_version_id"],
            "currencies": ["PHP"],
            "allowed_evidence_types": ["receipt"],
            "effective_from": "2026-01-01",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "expense_category_not_published"


async def test_policy_publication_branch_scope_denied(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    bootstrap = await _bootstrap_organization(expense_client, expense_settings)
    branch_id = next(
        branch["branch_id"] for branch in bootstrap["branches"] if branch["code"] == "CEB"
    )
    category = await _create_category(
        expense_client,
        expense_settings,
        code="CEB_CAT",
        effective_from="2026-01-01",
        idempotency_key="create-ceb-cat",
    )
    await _publish_category(
        expense_client,
        expense_settings,
        code="CEB_CAT",
        version=1,
        publisher="category-publisher",
        idempotency_key="publish-ceb-cat",
    )

    response = await expense_client.post(
        "/v1/finance/expense-policies",
        headers=_headers(
            expense_settings,
            subject="policy-creator",
            capabilities=["finance:expense-policy-create"],
            idempotency_key="create-ceb-policy",
        ),
        json={
            "policy_code": "CEB_POLICY",
            "name": "CEB Policy",
            "branch_id": branch_id,
            "category_version_id": category["expense_category_version_id"],
            "currencies": ["PHP"],
            "allowed_evidence_types": ["receipt"],
            "effective_from": "2026-01-01",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "operational_scope_required"


async def test_policy_publication_over_limit_rejected(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    category = await _create_category(
        expense_client,
        expense_settings,
        code="HIGH_VALUE",
        effective_from="2026-01-01",
        idempotency_key="create-high-value",
    )
    await _publish_category(
        expense_client,
        expense_settings,
        code="HIGH_VALUE",
        version=1,
        publisher="category-publisher",
        idempotency_key="publish-high-value",
    )

    policy = await _create_policy(
        expense_client,
        expense_settings,
        code="HIGH_VALUE_POLICY",
        category_version_id=category["expense_category_version_id"],
        effective_from="2026-01-01",
        max_amount="1000.00",
        idempotency_key="create-high-value-policy",
    )

    response = await expense_client.post(
        f"/v1/finance/expense-policies/HIGH_VALUE_POLICY/versions/{policy['version']}/publish",
        headers=_headers(
            expense_settings,
            subject="category-publisher-limited",
            capabilities=[
                "finance:expense-policy-publish",
                "finance:expense-policy-read",
            ],
            idempotency_key="publish-high-value-policy-limited",
        ),
        json={},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "approval_authority_required"


async def test_idempotent_category_publication(
    expense_client: AsyncClient,
    expense_settings: Settings,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    created = await _create_category(
        expense_client,
        expense_settings,
        code="IDEM",
        effective_from="2026-01-01",
        idempotency_key="create-idem",
    )

    first = await expense_client.post(
        f"/v1/finance/expense-categories/IDEM/versions/{created['version']}/publish",
        headers=_headers(
            expense_settings,
            subject="category-publisher",
            capabilities=[
                "finance:expense-category-publish",
                "finance:expense-category-read",
            ],
            idempotency_key="publish-idem",
        ),
        json={},
    )
    assert first.status_code == 200
    assert first.headers["x-idempotency-replayed"] == "false"

    second = await expense_client.post(
        f"/v1/finance/expense-categories/IDEM/versions/{created['version']}/publish",
        headers=_headers(
            expense_settings,
            subject="category-publisher",
            capabilities=[
                "finance:expense-category-publish",
                "finance:expense-category-read",
            ],
            idempotency_key="publish-idem",
        ),
        json={},
    )
    assert second.status_code == 200
    assert second.headers["x-idempotency-replayed"] == "true"
    assert (
        second.json()["expense_category_version_id"] == first.json()["expense_category_version_id"]
    )


async def test_published_category_is_immutable(
    expense_client: AsyncClient,
    expense_settings: Settings,
    postgres_url: str,
) -> None:
    await _bootstrap_organization(expense_client, expense_settings)
    await _create_category(
        expense_client,
        expense_settings,
        code="IMMUTABLE",
        effective_from="2026-01-01",
        idempotency_key="create-immutable",
    )
    await _publish_category(
        expense_client,
        expense_settings,
        code="IMMUTABLE",
        version=1,
        publisher="category-publisher",
        idempotency_key="publish-immutable",
    )

    engine = create_async_engine(postgres_url)
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE expense_categories SET name = 'Hacked' "
                    "WHERE category_code = 'IMMUTABLE'"
                )
            )
    await engine.dispose()
