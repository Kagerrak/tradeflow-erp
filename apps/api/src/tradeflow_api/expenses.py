from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_expense_category_creator,
    require_expense_category_publisher,
    require_expense_category_reader,
    require_expense_policy_creator,
    require_expense_policy_publisher,
    require_expense_policy_reader,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    branches,
    companies,
    expense_categories,
    expense_policies,
)

router = APIRouter(prefix="/v1/finance", tags=["finance"])


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttributionRules(CommandModel):
    cost_center_required: bool = False
    branch_required: bool = False
    project_allowed: bool = False
    supplier_allowed: bool = False
    employee_allowed: bool = False


class CreateExpenseCategoryCommand(CommandModel):
    category_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,49}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    allowed_evidence_types: list[str] = Field(min_length=1)
    attribution_rules: AttributionRules = Field(default_factory=AttributionRules)
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def valid_effective_range(self) -> CreateExpenseCategoryCommand:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from.")
        return self


class ExpenseCategoryVersionResponse(BaseModel):
    expense_category_version_id: UUID
    company_id: UUID
    category_code: str
    version: int
    name: str
    description: str | None
    allowed_evidence_types: list[str]
    attribution_rules: dict[str, Any]
    effective_from: date
    effective_to: date | None
    status: str
    created_by: str
    published_by: str | None
    created_at: str
    published_at: str | None


class CreateExpensePolicyCommand(CommandModel):
    policy_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,49}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    branch_id: UUID | None = None
    category_version_id: UUID
    max_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    currencies: list[str] = Field(min_length=1)
    requires_receipt: bool = True
    allowed_evidence_types: list[str] = Field(min_length=1)
    attribution_rules: AttributionRules = Field(default_factory=AttributionRules)
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def valid_effective_range(self) -> CreateExpensePolicyCommand:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from.")
        return self

    @model_validator(mode="after")
    def valid_currencies(self) -> CreateExpensePolicyCommand:
        for currency in self.currencies:
            if len(currency) != 3 or not currency.isalpha() or not currency.isupper():
                raise ValueError("currencies must be ISO 4217 codes.")
        return self


class ExpensePolicyVersionResponse(BaseModel):
    expense_policy_version_id: UUID
    company_id: UUID
    branch_id: UUID | None
    policy_code: str
    version: int
    name: str
    description: str | None
    category_version_id: UUID
    category_code: str
    max_amount: Decimal | None
    currencies: list[str]
    requires_receipt: bool
    allowed_evidence_types: list[str]
    attribution_rules: dict[str, Any]
    effective_from: date
    effective_to: date | None
    status: str
    created_by: str
    published_by: str | None
    created_at: str
    published_at: str | None


def _idempotency_key_header() -> Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)
]:
    return None


async def _load_company_id(session: AsyncSession) -> UUID:
    company_id = await session.scalar(select(companies.c.company_id))
    if company_id is None:
        raise AppError(
            status_code=404,
            code="company_not_configured",
            message="TradeFlow has no configured Company.",
        )
    return cast(UUID, company_id)


async def _require_branch_scope(
    session: AsyncSession,
    *,
    actor: AuthorizedUser,
    branch_id: UUID | None,
) -> None:
    if branch_id is None:
        return
    branch = await session.scalar(
        select(branches.c.branch_id).where(branches.c.branch_id == branch_id)
    )
    if branch is None:
        raise AppError(
            status_code=404,
            code="branch_not_found",
            message="The Branch does not exist.",
        )
    if branch_id not in actor.branch_ids:
        raise AppError(
            status_code=403,
            code="operational_scope_required",
            message="The Branch is outside the user's Operational Scope.",
        )


async def _require_expense_publish_authority(
    session: AsyncSession,
    *,
    actor: AuthorizedUser,
    capability: str,
    maker_subject: str,
    amount: Decimal | None,
    branch_id: UUID | None,
) -> UUID:
    if actor.subject == maker_subject:
        raise AppError(
            status_code=403,
            code="self_publication_forbidden",
            message="The same user cannot publish a version they created.",
        )

    query = select(approval_authorities).where(
        approval_authorities.c.user_subject == actor.subject,
        approval_authorities.c.capability_code == capability,
    )
    if branch_id is not None:
        query = query.where(approval_authorities.c.branch_id == branch_id)
    else:
        if actor.branch_ids:
            query = query.where(approval_authorities.c.branch_id.in_(actor.branch_ids))
        else:
            raise AppError(
                status_code=403,
                code="operational_scope_required",
                message="The user has no Branch assignment.",
            )

    row = (await session.execute(query)).mappings().one_or_none()
    if row is None:
        raise AppError(
            status_code=403,
            code="approval_authority_required",
            message=f"You do not have approval authority for {capability}.",
        )

    maximum_amount = row["maximum_amount"]
    if amount is not None and maximum_amount is not None and amount > maximum_amount:
        raise AppError(
            status_code=403,
            code="approval_limit_exceeded",
            message="The version amount exceeds your approval limit.",
        )

    return cast(UUID, row["approval_authority_id"])


async def _guard_no_overlap(
    session: AsyncSession,
    *,
    table: Any,
    code_column: Any,
    code_value: str,
    company_id: UUID,
    version_id: UUID,
    version_id_column: Any,
    effective_from: date,
    effective_to: date | None,
    branch_id: UUID | None = None,
) -> None:
    end = effective_to or date.max
    query = (
        select(func.count())
        .select_from(table)
        .where(
            table.c.status == "published",
            table.c.company_id == company_id,
            code_column == code_value,
            version_id_column != version_id,
            table.c.effective_from <= end,
            func.coalesce(table.c.effective_to, date.max) >= effective_from,
        )
    )
    if branch_id is not None:
        query = query.where(table.c.branch_id == branch_id)

    count = (await session.execute(query)).scalar_one()
    if count:
        raise AppError(
            status_code=409,
            code="effective_range_overlap",
            message="The effective range overlaps an existing published version.",
        )


async def _next_category_version(
    session: AsyncSession,
    company_id: UUID,
    category_code: str,
) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(expense_categories.c.version), 0) + 1).where(
            expense_categories.c.company_id == company_id,
            expense_categories.c.category_code == category_code,
        )
    )
    return int(result.scalar_one())


async def _next_policy_version(
    session: AsyncSession,
    company_id: UUID,
    branch_id: UUID | None,
    policy_code: str,
) -> int:
    where = [
        expense_policies.c.company_id == company_id,
        expense_policies.c.policy_code == policy_code,
    ]
    if branch_id is not None:
        where.append(expense_policies.c.branch_id == branch_id)
    else:
        where.append(expense_policies.c.branch_id.is_(None))
    result = await session.execute(
        select(func.coalesce(func.max(expense_policies.c.version), 0) + 1).where(*where)
    )
    return int(result.scalar_one())


def _category_response(row: Any) -> ExpenseCategoryVersionResponse:
    return ExpenseCategoryVersionResponse(
        expense_category_version_id=row["expense_category_version_id"],
        company_id=row["company_id"],
        category_code=row["category_code"],
        version=row["version"],
        name=row["name"],
        description=row["description"],
        allowed_evidence_types=list(row["allowed_evidence_types"]),
        attribution_rules=dict(row["attribution_rules"]),
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        status=row["status"],
        created_by=row["created_by"],
        published_by=row["published_by"],
        created_at=row["created_at"].isoformat(),
        published_at=row["published_at"].isoformat() if row["published_at"] is not None else None,
    )


def _policy_response(row: Any) -> ExpensePolicyVersionResponse:
    return ExpensePolicyVersionResponse(
        expense_policy_version_id=row["expense_policy_version_id"],
        company_id=row["company_id"],
        branch_id=row["branch_id"],
        policy_code=row["policy_code"],
        version=row["version"],
        name=row["name"],
        description=row["description"],
        category_version_id=row["category_version_id"],
        category_code=row["category_code"],
        max_amount=row["max_amount"],
        currencies=list(row["currencies"]),
        requires_receipt=row["requires_receipt"],
        allowed_evidence_types=list(row["allowed_evidence_types"]),
        attribution_rules=dict(row["attribution_rules"]),
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        status=row["status"],
        created_by=row["created_by"],
        published_by=row["published_by"],
        created_at=row["created_at"].isoformat(),
        published_at=row["published_at"].isoformat() if row["published_at"] is not None else None,
    )


@router.post(
    "/expense-categories",
    response_model=ExpenseCategoryVersionResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
async def create_expense_category(
    command: CreateExpenseCategoryCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_expense_category_creator)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> ExpenseCategoryVersionResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    request_hash = sha256(
        f"expense-category:{command.category_code}:".encode() + command.model_dump_json().encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return ExpenseCategoryVersionResponse.model_validate(replay)

        company_id = await _load_company_id(session)
        version = await _next_category_version(session, company_id, command.category_code)
        version_id = uuid4()
        created_at = datetime.now(UTC)
        await session.execute(
            insert(expense_categories).values(
                expense_category_version_id=version_id,
                company_id=company_id,
                category_code=command.category_code,
                version=version,
                name=command.name,
                description=command.description,
                allowed_evidence_types=command.allowed_evidence_types,
                attribution_rules=command.attribution_rules.model_dump(),
                effective_from=command.effective_from,
                effective_to=command.effective_to,
                status="draft",
                created_by=actor.subject,
                created_at=created_at,
            )
        )
        result = ExpenseCategoryVersionResponse(
            expense_category_version_id=version_id,
            company_id=company_id,
            category_code=command.category_code,
            version=version,
            name=command.name,
            description=command.description,
            allowed_evidence_types=command.allowed_evidence_types,
            attribution_rules=command.attribution_rules.model_dump(),
            effective_from=command.effective_from,
            effective_to=command.effective_to,
            status="draft",
            created_by=actor.subject,
            published_by=None,
            created_at=created_at.isoformat(),
            published_at=None,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    response.headers["X-Idempotency-Replayed"] = "false"
    return result


@router.post(
    "/expense-categories/{category_code}/versions/{version}/publish",
    response_model=ExpenseCategoryVersionResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def publish_expense_category(
    category_code: str,
    version: int,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_expense_category_publisher)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> ExpenseCategoryVersionResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    request_hash = sha256(
        f"expense-category-publish:{category_code}:{version}:".encode() + actor.subject.encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return ExpenseCategoryVersionResponse.model_validate(replay)

        company_id = await _load_company_id(session)
        row = (
            (
                await session.execute(
                    select(expense_categories).where(
                        expense_categories.c.company_id == company_id,
                        expense_categories.c.category_code == category_code,
                        expense_categories.c.version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AppError(
                status_code=404,
                code="expense_category_not_found",
                message="The Expense Category version does not exist.",
            )
        if row["status"] != "draft":
            raise AppError(
                status_code=409,
                code="stale_publication",
                message="The Expense Category version is not in a publishable state.",
            )

        await _require_expense_publish_authority(
            session,
            actor=actor,
            capability="finance:expense-category-publish",
            maker_subject=row["created_by"],
            amount=None,
            branch_id=None,
        )
        await _guard_no_overlap(
            session,
            table=expense_categories,
            code_column=expense_categories.c.category_code,
            code_value=category_code,
            company_id=company_id,
            version_id=row["expense_category_version_id"],
            version_id_column=expense_categories.c.expense_category_version_id,
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
        )

        published_at = datetime.now(UTC)
        category_version_id = row["expense_category_version_id"]
        await session.execute(
            update(expense_categories)
            .where(expense_categories.c.expense_category_version_id == category_version_id)
            .values(
                status="published",
                published_by=actor.subject,
                published_at=published_at,
            )
        )
        result = _category_response(
            {
                **dict(row),
                "status": "published",
                "published_by": actor.subject,
                "published_at": published_at,
            }
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    response.headers["X-Idempotency-Replayed"] = "false"
    return result


@router.get(
    "/expense-categories",
    response_model=list[ExpenseCategoryVersionResponse],
    responses=error_responses(400, 401, 403, 500),
)
async def list_expense_categories(
    actor: Annotated[AuthorizedUser, Depends(require_expense_category_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    status: Annotated[str | None, Query(pattern=r"^(draft|published)$")] = None,
) -> list[ExpenseCategoryVersionResponse]:
    company_id = await _load_company_id(session)
    query = select(expense_categories).where(expense_categories.c.company_id == company_id)
    if status is not None:
        query = query.where(expense_categories.c.status == status)
    query = query.order_by(
        expense_categories.c.category_code,
        expense_categories.c.version.desc(),
    )
    rows = (await session.execute(query)).mappings()
    return [_category_response(row) for row in rows]


@router.get(
    "/expense-categories/{category_code}",
    response_model=list[ExpenseCategoryVersionResponse],
    responses=error_responses(400, 401, 403, 404, 500),
)
async def get_expense_category_versions(
    category_code: str,
    actor: Annotated[AuthorizedUser, Depends(require_expense_category_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[ExpenseCategoryVersionResponse]:
    company_id = await _load_company_id(session)
    rows = (
        await session.execute(
            select(expense_categories)
            .where(
                expense_categories.c.company_id == company_id,
                expense_categories.c.category_code == category_code,
            )
            .order_by(expense_categories.c.version.desc())
        )
    ).mappings()
    results = list(rows)
    if not results:
        raise AppError(
            status_code=404,
            code="expense_category_not_found",
            message="The Expense Category does not exist.",
        )
    return [_category_response(row) for row in results]


@router.post(
    "/expense-policies",
    response_model=ExpensePolicyVersionResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
async def create_expense_policy(
    command: CreateExpensePolicyCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_expense_policy_creator)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> ExpensePolicyVersionResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    request_hash = sha256(
        f"expense-policy:{command.policy_code}:".encode() + command.model_dump_json().encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return ExpensePolicyVersionResponse.model_validate(replay)

        company_id = await _load_company_id(session)
        await _require_branch_scope(session, actor=actor, branch_id=command.branch_id)

        category_row = (
            (
                await session.execute(
                    select(
                        expense_categories.c.expense_category_version_id,
                        expense_categories.c.category_code,
                        expense_categories.c.status,
                    ).where(
                        expense_categories.c.expense_category_version_id
                        == command.category_version_id,
                        expense_categories.c.company_id == company_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if category_row is None:
            raise AppError(
                status_code=404,
                code="expense_category_not_found",
                message="The referenced Expense Category version does not exist.",
            )
        if category_row["status"] != "published":
            raise AppError(
                status_code=409,
                code="expense_category_not_published",
                message="Expense Policies can only reference published Expense Categories.",
            )

        version = await _next_policy_version(
            session, company_id, command.branch_id, command.policy_code
        )
        version_id = uuid4()
        created_at = datetime.now(UTC)
        await session.execute(
            insert(expense_policies).values(
                expense_policy_version_id=version_id,
                company_id=company_id,
                branch_id=command.branch_id,
                policy_code=command.policy_code,
                version=version,
                name=command.name,
                description=command.description,
                category_version_id=category_row["expense_category_version_id"],
                category_code=category_row["category_code"],
                max_amount=command.max_amount,
                currencies=command.currencies,
                requires_receipt=command.requires_receipt,
                allowed_evidence_types=command.allowed_evidence_types,
                attribution_rules=command.attribution_rules.model_dump(),
                effective_from=command.effective_from,
                effective_to=command.effective_to,
                status="draft",
                created_by=actor.subject,
                created_at=created_at,
            )
        )
        result = ExpensePolicyVersionResponse(
            expense_policy_version_id=version_id,
            company_id=company_id,
            branch_id=command.branch_id,
            policy_code=command.policy_code,
            version=version,
            name=command.name,
            description=command.description,
            category_version_id=category_row["expense_category_version_id"],
            category_code=category_row["category_code"],
            max_amount=command.max_amount,
            currencies=command.currencies,
            requires_receipt=command.requires_receipt,
            allowed_evidence_types=command.allowed_evidence_types,
            attribution_rules=command.attribution_rules.model_dump(),
            effective_from=command.effective_from,
            effective_to=command.effective_to,
            status="draft",
            created_by=actor.subject,
            published_by=None,
            created_at=created_at.isoformat(),
            published_at=None,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    response.headers["X-Idempotency-Replayed"] = "false"
    return result


@router.post(
    "/expense-policies/{policy_code}/versions/{version}/publish",
    response_model=ExpensePolicyVersionResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def publish_expense_policy(
    policy_code: str,
    version: int,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_expense_policy_publisher)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> ExpensePolicyVersionResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    request_hash = sha256(
        f"expense-policy-publish:{policy_code}:{version}:".encode() + actor.subject.encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return ExpensePolicyVersionResponse.model_validate(replay)

        company_id = await _load_company_id(session)
        row = (
            (
                await session.execute(
                    select(expense_policies).where(
                        expense_policies.c.company_id == company_id,
                        expense_policies.c.policy_code == policy_code,
                        expense_policies.c.version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AppError(
                status_code=404,
                code="expense_policy_not_found",
                message="The Expense Policy version does not exist.",
            )
        if row["status"] != "draft":
            raise AppError(
                status_code=409,
                code="stale_publication",
                message="The Expense Policy version is not in a publishable state.",
            )

        await _require_branch_scope(session, actor=actor, branch_id=row["branch_id"])
        await _require_expense_publish_authority(
            session,
            actor=actor,
            capability="finance:expense-policy-publish",
            maker_subject=row["created_by"],
            amount=row["max_amount"],
            branch_id=row["branch_id"],
        )
        await _guard_no_overlap(
            session,
            table=expense_policies,
            code_column=expense_policies.c.policy_code,
            code_value=policy_code,
            company_id=company_id,
            version_id=row["expense_policy_version_id"],
            version_id_column=expense_policies.c.expense_policy_version_id,
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
            branch_id=row["branch_id"],
        )

        published_at = datetime.now(UTC)
        policy_version_id = row["expense_policy_version_id"]
        await session.execute(
            update(expense_policies)
            .where(expense_policies.c.expense_policy_version_id == policy_version_id)
            .values(
                status="published",
                published_by=actor.subject,
                published_at=published_at,
            )
        )
        result = _policy_response(
            {
                **dict(row),
                "status": "published",
                "published_by": actor.subject,
                "published_at": published_at,
            }
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    response.headers["X-Idempotency-Replayed"] = "false"
    return result


@router.get(
    "/expense-policies",
    response_model=list[ExpensePolicyVersionResponse],
    responses=error_responses(400, 401, 403, 500),
)
async def list_expense_policies(
    actor: Annotated[AuthorizedUser, Depends(require_expense_policy_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    status: Annotated[str | None, Query(pattern=r"^(draft|published)$")] = None,
) -> list[ExpensePolicyVersionResponse]:
    company_id = await _load_company_id(session)
    query = select(expense_policies).where(expense_policies.c.company_id == company_id)
    if status is not None:
        query = query.where(expense_policies.c.status == status)
    query = query.order_by(
        expense_policies.c.policy_code,
        expense_policies.c.version.desc(),
    )
    rows = (await session.execute(query)).mappings()
    return [_policy_response(row) for row in rows]


@router.get(
    "/expense-policies/{policy_code}",
    response_model=list[ExpensePolicyVersionResponse],
    responses=error_responses(400, 401, 403, 404, 500),
)
async def get_expense_policy_versions(
    policy_code: str,
    actor: Annotated[AuthorizedUser, Depends(require_expense_policy_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[ExpensePolicyVersionResponse]:
    company_id = await _load_company_id(session)
    rows = (
        await session.execute(
            select(expense_policies)
            .where(
                expense_policies.c.company_id == company_id,
                expense_policies.c.policy_code == policy_code,
            )
            .order_by(expense_policies.c.version.desc())
        )
    ).mappings()
    results = list(rows)
    if not results:
        raise AppError(
            status_code=404,
            code="expense_policy_not_found",
            message="The Expense Policy does not exist.",
        )
    return [_policy_response(row) for row in results]
