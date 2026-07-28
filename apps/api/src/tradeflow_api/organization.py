from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    CurrentUser,
    load_authorized_user,
    require_organization_administrator,
    require_organization_bootstrapper,
)
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    branches,
    capabilities,
    companies,
    platform_command_receipts,
    role_template_capabilities,
    role_templates,
    user_branch_scopes,
    user_role_templates,
    user_warehouse_scopes,
    users,
    warehouses,
)

router = APIRouter(prefix="/v1/organization", tags=["organization"])


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompanyInput(CommandModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,29}$")
    name: str = Field(min_length=1, max_length=200)
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")


class WarehouseInput(CommandModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,29}$")
    name: str = Field(min_length=1, max_length=200)


class BranchInput(CommandModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,29}$")
    name: str = Field(min_length=1, max_length=200)
    warehouses: list[WarehouseInput] = Field(min_length=1)


class RoleTemplateInput(CommandModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,49}$")
    name: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(min_length=1)


class ApprovalAuthorityInput(CommandModel):
    capability: str = Field(min_length=1, max_length=100)
    branch_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,29}$")
    maximum_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    maximum_percentage: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
        max_digits=9,
        decimal_places=6,
    )
    maker_checker_required: bool = True


class UserInput(CommandModel):
    subject: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    is_operations_administrator: bool = False
    role_template_codes: list[str] = Field(default_factory=list)
    branch_codes: list[str] = Field(default_factory=list)
    warehouse_codes: list[str] = Field(default_factory=list)
    approval_authorities: list[ApprovalAuthorityInput] = Field(default_factory=list)


class OrganizationBootstrapCommand(CommandModel):
    company: CompanyInput
    branches: list[BranchInput] = Field(min_length=1)
    role_templates: list[RoleTemplateInput] = Field(min_length=1)
    users: list[UserInput] = Field(min_length=1)


class CompanyResponse(BaseModel):
    code: str
    name: str
    base_currency: str
    version: int


class WarehouseResponse(BaseModel):
    warehouse_id: UUID
    code: str
    name: str
    is_active: bool
    version: int


class BranchResponse(BaseModel):
    branch_id: UUID
    code: str
    name: str
    is_active: bool
    version: int
    warehouses: list[WarehouseResponse]


class OrganizationBootstrapResponse(BaseModel):
    company: CompanyResponse
    branches: list[BranchResponse]
    configured_users: int


class UpdateCompanyCommand(CommandModel):
    name: str = Field(min_length=1, max_length=200)
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")


class LifecycleCommand(CommandModel):
    is_active: bool


class BranchLifecycleResponse(BaseModel):
    branch_id: UUID
    code: str
    name: str
    is_active: bool
    version: int


class ScopeUserResponse(BaseModel):
    subject: str
    display_name: str
    is_operations_administrator: bool


class ScopeBranchResponse(BaseModel):
    branch_id: UUID
    code: str
    name: str
    is_active: bool
    version: int


class ScopeWarehouseResponse(BaseModel):
    warehouse_id: UUID
    branch_id: UUID
    code: str
    name: str
    is_active: bool
    version: int


class OrganizationScopeResponse(BaseModel):
    user: ScopeUserResponse
    capabilities: list[str]
    branches: list[ScopeBranchResponse]
    warehouses: list[ScopeWarehouseResponse]


class ConfigureRoleTemplateCommand(CommandModel):
    name: str = Field(min_length=1, max_length=200)
    is_active: bool = True
    capabilities: list[str] = Field(min_length=1)


class RoleTemplateResponse(BaseModel):
    code: str
    name: str
    is_active: bool
    capabilities: list[str]
    version: int


class ConfigureUserCommand(CommandModel):
    display_name: str = Field(min_length=1, max_length=200)
    is_operations_administrator: bool = False
    is_active: bool = True
    role_template_codes: list[str] = Field(default_factory=list)
    branch_codes: list[str] = Field(default_factory=list)
    warehouse_codes: list[str] = Field(default_factory=list)
    approval_authorities: list[ApprovalAuthorityInput] = Field(default_factory=list)


class UserConfigurationResponse(BaseModel):
    subject: str
    display_name: str
    is_operations_administrator: bool
    is_active: bool
    role_template_codes: list[str]
    branch_codes: list[str]
    warehouse_codes: list[str]
    approval_authorities: list[ApprovalAuthorityInput]
    version: int


def invalid_assignment(message: str) -> AppError:
    return AppError(
        status_code=422,
        code="invalid_organization_assignment",
        message=message,
    )


async def get_command_replay(
    session: AsyncSession,
    *,
    idempotency_key: str,
    request_hash: str,
) -> dict[str, object] | None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:idempotency_key))"),
        {"idempotency_key": idempotency_key},
    )
    receipt = (
        await session.execute(
            select(
                platform_command_receipts.c.request_hash,
                platform_command_receipts.c.response_json,
            ).where(platform_command_receipts.c.idempotency_key == idempotency_key)
        )
    ).one_or_none()
    if receipt is None:
        return None
    stored_hash, stored_response = receipt
    if stored_hash != request_hash:
        raise AppError(
            status_code=409,
            code="idempotency_conflict",
            message="Idempotency-Key was already used for another command.",
        )
    return dict(stored_response)


async def store_command_result(
    session: AsyncSession,
    *,
    actor_subject: str,
    idempotency_key: str,
    request_hash: str,
    result: BaseModel,
) -> None:
    await session.execute(
        insert(platform_command_receipts).values(
            command_id=uuid4(),
            idempotency_key=idempotency_key,
            actor_subject=actor_subject,
            request_hash=request_hash,
            response_json=result.model_dump(mode="json"),
        )
    )


def require_command_headers(
    *,
    expected_version: int | None,
    idempotency_key: str | None,
) -> tuple[int, str]:
    if expected_version is None:
        raise AppError(
            status_code=400,
            code="expected_version_required",
            message="If-Match is required for this command.",
        )
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    return expected_version, idempotency_key


def require_configuration_headers(
    *,
    expected_version: int | None,
    idempotency_key: str | None,
) -> tuple[int, str]:
    if expected_version is None:
        raise AppError(
            status_code=400,
            code="expected_version_required",
            message="If-Match is required; use 0 when creating a configuration.",
        )
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    return expected_version, idempotency_key


@router.get(
    "/scope",
    response_model=OrganizationScopeResponse,
    responses=error_responses(401, 403, 500),
)
async def get_organization_scope(
    actor: Annotated[AuthorizedUser, Depends(load_authorized_user)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> OrganizationScopeResponse:
    scoped_branches = []
    if actor.branch_ids:
        scoped_branches = list(
            (
                await session.execute(
                    select(
                        branches.c.branch_id,
                        branches.c.code,
                        branches.c.name,
                        branches.c.is_active,
                        branches.c.version,
                    )
                    .where(branches.c.branch_id.in_(actor.branch_ids))
                    .order_by(branches.c.code)
                )
            ).mappings()
        )

    scoped_warehouses = []
    if actor.warehouse_ids:
        scoped_warehouses = list(
            (
                await session.execute(
                    select(
                        warehouses.c.warehouse_id,
                        warehouses.c.branch_id,
                        warehouses.c.code,
                        warehouses.c.name,
                        warehouses.c.is_active,
                        warehouses.c.version,
                    )
                    .where(warehouses.c.warehouse_id.in_(actor.warehouse_ids))
                    .order_by(warehouses.c.code)
                )
            ).mappings()
        )

    return OrganizationScopeResponse(
        user=ScopeUserResponse(
            subject=actor.subject,
            display_name=actor.display_name,
            is_operations_administrator=actor.is_operations_administrator,
        ),
        capabilities=list(actor.capabilities),
        branches=[ScopeBranchResponse.model_validate(branch) for branch in scoped_branches],
        warehouses=[
            ScopeWarehouseResponse.model_validate(warehouse) for warehouse in scoped_warehouses
        ],
    )


@router.put(
    "/role-templates/{role_code}",
    response_model=RoleTemplateResponse,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
async def configure_role_template(
    role_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,49}$")],
    command: ConfigureRoleTemplateCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_organization_administrator)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    expected_version: Annotated[int | None, Header(alias="If-Match", ge=0)] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> RoleTemplateResponse:
    expected_version, idempotency_key = require_configuration_headers(
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    request_hash = sha256(
        f"role:{role_code}:{expected_version}:".encode() + command.model_dump_json().encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return RoleTemplateResponse.model_validate(replay)

        existing = (
            await session.execute(
                select(
                    role_templates.c.role_template_id,
                    role_templates.c.version,
                )
                .where(role_templates.c.code == role_code)
                .with_for_update()
            )
        ).one_or_none()
        if existing is None:
            if expected_version != 0:
                raise AppError(
                    status_code=409,
                    code="optimistic_version_conflict",
                    message="The Role Template does not exist; create it with If-Match 0.",
                )
            role_template_id = uuid4()
            version = 1
            await session.execute(
                insert(role_templates).values(
                    role_template_id=role_template_id,
                    code=role_code,
                    name=command.name,
                    is_active=command.is_active,
                    version=version,
                )
            )
            response.status_code = 201
        else:
            if expected_version != existing.version:
                raise AppError(
                    status_code=409,
                    code="optimistic_version_conflict",
                    message="The Role Template changed; reload it before retrying.",
                )
            role_template_id = existing.role_template_id
            version = existing.version + 1
            await session.execute(
                update(role_templates)
                .where(role_templates.c.role_template_id == role_template_id)
                .values(name=command.name, is_active=command.is_active, version=version)
            )
            await session.execute(
                delete(role_template_capabilities).where(
                    role_template_capabilities.c.role_template_id == role_template_id
                )
            )

        existing_capabilities = set(
            (
                await session.execute(
                    select(capabilities.c.code).where(capabilities.c.code.in_(command.capabilities))
                )
            ).scalars()
        )
        for capability in sorted(set(command.capabilities) - existing_capabilities):
            await session.execute(insert(capabilities).values(code=capability))
        for capability in sorted(set(command.capabilities)):
            await session.execute(
                insert(role_template_capabilities).values(
                    role_template_id=role_template_id,
                    capability_code=capability,
                )
            )

        result = RoleTemplateResponse(
            code=role_code,
            name=command.name,
            is_active=command.is_active,
            capabilities=sorted(set(command.capabilities)),
            version=version,
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


@router.put(
    "/users/{subject}",
    response_model=UserConfigurationResponse,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
async def configure_user(
    subject: Annotated[str, Field(min_length=1, max_length=200)],
    command: ConfigureUserCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_organization_administrator)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    expected_version: Annotated[int | None, Header(alias="If-Match", ge=0)] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> UserConfigurationResponse:
    expected_version, idempotency_key = require_configuration_headers(
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    request_hash = sha256(
        f"user:{subject}:{expected_version}:".encode() + command.model_dump_json().encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return UserConfigurationResponse.model_validate(replay)

        existing = (
            await session.execute(
                select(users.c.version).where(users.c.subject == subject).with_for_update()
            )
        ).one_or_none()
        if existing is None:
            if expected_version != 0:
                raise AppError(
                    status_code=409,
                    code="optimistic_version_conflict",
                    message="The User does not exist; create it with If-Match 0.",
                )
            version = 1
            await session.execute(
                insert(users).values(
                    subject=subject,
                    display_name=command.display_name,
                    is_operations_administrator=command.is_operations_administrator,
                    is_active=command.is_active,
                    version=version,
                )
            )
            response.status_code = 201
        else:
            if expected_version != existing.version:
                raise AppError(
                    status_code=409,
                    code="optimistic_version_conflict",
                    message="The User changed; reload it before retrying.",
                )
            version = existing.version + 1
            await session.execute(
                update(users)
                .where(users.c.subject == subject)
                .values(
                    display_name=command.display_name,
                    is_operations_administrator=command.is_operations_administrator,
                    is_active=command.is_active,
                    version=version,
                )
            )
            await session.execute(
                delete(approval_authorities).where(approval_authorities.c.user_subject == subject)
            )
            await session.execute(
                delete(user_warehouse_scopes).where(user_warehouse_scopes.c.user_subject == subject)
            )
            await session.execute(
                delete(user_branch_scopes).where(user_branch_scopes.c.user_subject == subject)
            )
            await session.execute(
                delete(user_role_templates).where(user_role_templates.c.user_subject == subject)
            )

        role_rows = (
            await session.execute(
                select(role_templates.c.code, role_templates.c.role_template_id).where(
                    role_templates.c.code.in_(command.role_template_codes),
                    role_templates.c.is_active.is_(True),
                )
            )
        ).all()
        role_ids = {row.code: row.role_template_id for row in role_rows}
        branch_rows = (
            await session.execute(
                select(branches.c.code, branches.c.branch_id).where(
                    branches.c.code.in_(command.branch_codes)
                )
            )
        ).all()
        branch_ids = {row.code: row.branch_id for row in branch_rows}
        warehouse_rows = (
            await session.execute(
                select(
                    warehouses.c.code,
                    warehouses.c.warehouse_id,
                    warehouses.c.branch_id,
                ).where(warehouses.c.code.in_(command.warehouse_codes))
            )
        ).all()
        warehouse_ids = {row.code: row.warehouse_id for row in warehouse_rows}
        if set(role_ids) != set(command.role_template_codes):
            raise invalid_assignment("Assignment references an unknown or inactive Role Template.")
        if set(branch_ids) != set(command.branch_codes):
            raise invalid_assignment("Assignment references an unknown Branch.")
        if set(warehouse_ids) != set(command.warehouse_codes):
            raise invalid_assignment("Assignment references an unknown Warehouse.")
        assigned_branch_ids = set(branch_ids.values())
        if any(row.branch_id not in assigned_branch_ids for row in warehouse_rows):
            raise invalid_assignment("A Warehouse assignment requires its Branch assignment.")

        assigned_capabilities = set(
            (
                await session.execute(
                    select(role_template_capabilities.c.capability_code).where(
                        role_template_capabilities.c.role_template_id.in_(role_ids.values())
                    )
                )
            ).scalars()
        )
        for authority in command.approval_authorities:
            if authority.capability not in assigned_capabilities:
                raise invalid_assignment(
                    "Approval Authority requires the capability through an assigned Role Template."
                )
            if authority.branch_code not in branch_ids:
                raise invalid_assignment(
                    "Approval Authority requires the matching Branch assignment."
                )

        for role_template_id in role_ids.values():
            await session.execute(
                insert(user_role_templates).values(
                    user_subject=subject,
                    role_template_id=role_template_id,
                )
            )
        for branch_id in branch_ids.values():
            await session.execute(
                insert(user_branch_scopes).values(
                    user_subject=subject,
                    branch_id=branch_id,
                )
            )
        for warehouse_id in warehouse_ids.values():
            await session.execute(
                insert(user_warehouse_scopes).values(
                    user_subject=subject,
                    warehouse_id=warehouse_id,
                )
            )
        for authority in command.approval_authorities:
            await session.execute(
                insert(approval_authorities).values(
                    approval_authority_id=uuid4(),
                    user_subject=subject,
                    capability_code=authority.capability,
                    branch_id=branch_ids[authority.branch_code],
                    maximum_amount=authority.maximum_amount,
                    maximum_percentage=authority.maximum_percentage,
                    maker_checker_required=authority.maker_checker_required,
                )
            )

        result = UserConfigurationResponse(
            subject=subject,
            display_name=command.display_name,
            is_operations_administrator=command.is_operations_administrator,
            is_active=command.is_active,
            role_template_codes=sorted(set(command.role_template_codes)),
            branch_codes=sorted(set(command.branch_codes)),
            warehouse_codes=sorted(set(command.warehouse_codes)),
            approval_authorities=sorted(
                command.approval_authorities,
                key=lambda authority: (authority.capability, authority.branch_code),
            ),
            version=version,
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


@router.patch(
    "/company",
    response_model=CompanyResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def update_company(
    command: UpdateCompanyCommand,
    response: Response,
    actor: Annotated[
        AuthorizedUser,
        Depends(require_organization_administrator),
    ],
    session: Annotated[
        AsyncSession,
        Depends(get_database_session, use_cache=False),
    ],
    expected_version: Annotated[
        int | None,
        Header(alias="If-Match", ge=1),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CompanyResponse:
    expected_version, idempotency_key = require_command_headers(
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    request_hash = sha256(
        f"company:{expected_version}:".encode() + command.model_dump_json().encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return CompanyResponse.model_validate(replay)

        company = (
            await session.execute(
                select(
                    companies.c.code,
                    companies.c.name,
                    companies.c.base_currency,
                    companies.c.version,
                ).with_for_update()
            )
        ).one_or_none()
        if company is None:
            raise AppError(
                status_code=404,
                code="company_not_configured",
                message="TradeFlow has no configured Company.",
            )
        if company.version != expected_version:
            raise AppError(
                status_code=409,
                code="optimistic_version_conflict",
                message="The Company changed; reload it before retrying.",
            )
        if command.base_currency != company.base_currency:
            raise AppError(
                status_code=409,
                code="base_currency_immutable",
                message="Company Base Currency cannot be changed.",
            )

        version = company.version + 1
        await session.execute(
            update(companies).values(
                name=command.name,
                version=version,
            )
        )
        result = CompanyResponse(
            code=company.code,
            name=command.name,
            base_currency=company.base_currency,
            version=version,
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


@router.patch(
    "/branches/{branch_id}",
    response_model=BranchLifecycleResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def update_branch_lifecycle(
    branch_id: UUID,
    command: LifecycleCommand,
    response: Response,
    actor: Annotated[
        AuthorizedUser,
        Depends(require_organization_administrator),
    ],
    session: Annotated[
        AsyncSession,
        Depends(get_database_session, use_cache=False),
    ],
    expected_version: Annotated[
        int | None,
        Header(alias="If-Match", ge=1),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> BranchLifecycleResponse:
    expected_version, idempotency_key = require_command_headers(
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    request_hash = sha256(
        f"branch:{branch_id}:{expected_version}:".encode() + command.model_dump_json().encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            branch_exists = await session.scalar(
                select(branches.c.branch_id).where(branches.c.branch_id == branch_id)
            )
            if branch_exists is None:
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
            response.headers["X-Idempotency-Replayed"] = "true"
            return BranchLifecycleResponse.model_validate(replay)

        branch = (
            await session.execute(
                select(
                    branches.c.code,
                    branches.c.name,
                    branches.c.version,
                )
                .where(branches.c.branch_id == branch_id)
                .with_for_update()
            )
        ).one_or_none()
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
        if branch.version != expected_version:
            raise AppError(
                status_code=409,
                code="optimistic_version_conflict",
                message="The Branch changed; reload it before retrying.",
            )

        version = branch.version + 1
        await session.execute(
            update(branches)
            .where(branches.c.branch_id == branch_id)
            .values(is_active=command.is_active, version=version)
        )
        result = BranchLifecycleResponse(
            branch_id=branch_id,
            code=branch.code,
            name=branch.name,
            is_active=command.is_active,
            version=version,
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


@router.patch(
    "/warehouses/{warehouse_id}",
    response_model=WarehouseResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def update_warehouse_lifecycle(
    warehouse_id: UUID,
    command: LifecycleCommand,
    response: Response,
    actor: Annotated[
        AuthorizedUser,
        Depends(require_organization_administrator),
    ],
    session: Annotated[
        AsyncSession,
        Depends(get_database_session, use_cache=False),
    ],
    expected_version: Annotated[
        int | None,
        Header(alias="If-Match", ge=1),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> WarehouseResponse:
    expected_version, idempotency_key = require_command_headers(
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    request_hash = sha256(
        f"warehouse:{warehouse_id}:{expected_version}:".encode()
        + command.model_dump_json().encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            current_warehouse = (
                await session.execute(
                    select(warehouses.c.branch_id).where(warehouses.c.warehouse_id == warehouse_id)
                )
            ).one_or_none()
            if current_warehouse is None:
                raise AppError(
                    status_code=404,
                    code="warehouse_not_found",
                    message="The Warehouse does not exist.",
                )
            if (
                warehouse_id not in actor.warehouse_ids
                or current_warehouse.branch_id not in actor.branch_ids
            ):
                raise AppError(
                    status_code=403,
                    code="operational_scope_required",
                    message="The Warehouse is outside the user's Operational Scope.",
                )
            response.headers["X-Idempotency-Replayed"] = "true"
            return WarehouseResponse.model_validate(replay)

        warehouse = (
            await session.execute(
                select(
                    warehouses.c.branch_id,
                    warehouses.c.code,
                    warehouses.c.name,
                    warehouses.c.version,
                )
                .where(warehouses.c.warehouse_id == warehouse_id)
                .with_for_update()
            )
        ).one_or_none()
        if warehouse is None:
            raise AppError(
                status_code=404,
                code="warehouse_not_found",
                message="The Warehouse does not exist.",
            )
        if warehouse_id not in actor.warehouse_ids or warehouse.branch_id not in actor.branch_ids:
            raise AppError(
                status_code=403,
                code="operational_scope_required",
                message="The Warehouse is outside the user's Operational Scope.",
            )
        if warehouse.version != expected_version:
            raise AppError(
                status_code=409,
                code="optimistic_version_conflict",
                message="The Warehouse changed; reload it before retrying.",
            )

        version = warehouse.version + 1
        await session.execute(
            update(warehouses)
            .where(warehouses.c.warehouse_id == warehouse_id)
            .values(is_active=command.is_active, version=version)
        )
        result = WarehouseResponse(
            warehouse_id=warehouse_id,
            code=warehouse.code,
            name=warehouse.name,
            is_active=command.is_active,
            version=version,
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
    "/bootstrap",
    response_model=OrganizationBootstrapResponse,
    responses=error_responses(400, 401, 403, 409, 422, 500),
    status_code=201,
)
async def bootstrap_organization(
    command: OrganizationBootstrapCommand,
    response: Response,
    actor: Annotated[CurrentUser, Depends(require_organization_bootstrapper)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> OrganizationBootstrapResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = sha256(command.model_dump_json().encode()).hexdigest()
    async with session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:idempotency_key))"),
            {"idempotency_key": idempotency_key},
        )
        receipt = (
            await session.execute(
                select(
                    platform_command_receipts.c.request_hash,
                    platform_command_receipts.c.response_json,
                ).where(platform_command_receipts.c.idempotency_key == idempotency_key)
            )
        ).one_or_none()
        if receipt is not None:
            stored_hash, stored_response = receipt
            if stored_hash != request_hash:
                raise AppError(
                    status_code=409,
                    code="idempotency_conflict",
                    message="Idempotency-Key was already used for another command.",
                )
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return OrganizationBootstrapResponse.model_validate(stored_response)

        existing_company = await session.scalar(select(companies.c.company_id).limit(1))
        if existing_company is not None:
            raise AppError(
                status_code=409,
                code="company_already_configured",
                message="TradeFlow already has its single Company configured.",
            )

        company_id = uuid4()
        await session.execute(
            insert(companies).values(
                company_id=company_id,
                singleton_key="tradeflow",
                code=command.company.code,
                name=command.company.name,
                base_currency=command.company.base_currency,
            )
        )

        branch_ids: dict[str, UUID] = {}
        warehouse_ids: dict[str, UUID] = {}
        branch_responses: list[BranchResponse] = []
        for branch in command.branches:
            if branch.code in branch_ids:
                raise invalid_assignment(f"Branch code '{branch.code}' is duplicated.")
            branch_id = uuid4()
            branch_ids[branch.code] = branch_id
            await session.execute(
                insert(branches).values(
                    branch_id=branch_id,
                    company_id=company_id,
                    code=branch.code,
                    name=branch.name,
                )
            )
            warehouse_responses: list[WarehouseResponse] = []
            for warehouse in branch.warehouses:
                if warehouse.code in warehouse_ids:
                    raise invalid_assignment(f"Warehouse code '{warehouse.code}' is duplicated.")
                warehouse_id = uuid4()
                warehouse_ids[warehouse.code] = warehouse_id
                await session.execute(
                    insert(warehouses).values(
                        warehouse_id=warehouse_id,
                        branch_id=branch_id,
                        code=warehouse.code,
                        name=warehouse.name,
                    )
                )
                warehouse_responses.append(
                    WarehouseResponse(
                        warehouse_id=warehouse_id,
                        code=warehouse.code,
                        name=warehouse.name,
                        is_active=True,
                        version=1,
                    )
                )
            branch_responses.append(
                BranchResponse(
                    branch_id=branch_id,
                    code=branch.code,
                    name=branch.name,
                    is_active=True,
                    version=1,
                    warehouses=warehouse_responses,
                )
            )

        capability_codes = {
            capability for role in command.role_templates for capability in role.capabilities
        } | {
            authority.capability
            for user in command.users
            for authority in user.approval_authorities
        }
        for capability_code in sorted(capability_codes):
            await session.execute(insert(capabilities).values(code=capability_code))

        role_ids: dict[str, UUID] = {}
        for role in command.role_templates:
            if role.code in role_ids:
                raise invalid_assignment(f"Role Template code '{role.code}' is duplicated.")
            role_id = uuid4()
            role_ids[role.code] = role_id
            await session.execute(
                insert(role_templates).values(
                    role_template_id=role_id,
                    code=role.code,
                    name=role.name,
                )
            )
            for capability_code in sorted(set(role.capabilities)):
                await session.execute(
                    insert(role_template_capabilities).values(
                        role_template_id=role_id,
                        capability_code=capability_code,
                    )
                )

        seen_subjects: set[str] = set()
        for configured_user in command.users:
            if configured_user.subject in seen_subjects:
                raise invalid_assignment(f"User subject '{configured_user.subject}' is duplicated.")
            seen_subjects.add(configured_user.subject)
            await session.execute(
                insert(users).values(
                    subject=configured_user.subject,
                    display_name=configured_user.display_name,
                    is_operations_administrator=(configured_user.is_operations_administrator),
                )
            )
            try:
                assigned_role_ids = {role_ids[code] for code in configured_user.role_template_codes}
                assigned_branch_ids = {branch_ids[code] for code in configured_user.branch_codes}
                assigned_warehouse_ids = {
                    warehouse_ids[code] for code in configured_user.warehouse_codes
                }
            except KeyError as error:
                raise invalid_assignment(
                    f"Assignment references unknown code '{error.args[0]}'."
                ) from error

            for role_id in assigned_role_ids:
                await session.execute(
                    insert(user_role_templates).values(
                        user_subject=configured_user.subject,
                        role_template_id=role_id,
                    )
                )
            for branch_id in assigned_branch_ids:
                await session.execute(
                    insert(user_branch_scopes).values(
                        user_subject=configured_user.subject,
                        branch_id=branch_id,
                    )
                )
            for warehouse_id in assigned_warehouse_ids:
                await session.execute(
                    insert(user_warehouse_scopes).values(
                        user_subject=configured_user.subject,
                        warehouse_id=warehouse_id,
                    )
                )
            for authority in configured_user.approval_authorities:
                try:
                    authority_branch_id = branch_ids[authority.branch_code]
                except KeyError as error:
                    raise invalid_assignment(
                        f"Approval Authority references unknown Branch '{authority.branch_code}'."
                    ) from error
                await session.execute(
                    insert(approval_authorities).values(
                        approval_authority_id=uuid4(),
                        user_subject=configured_user.subject,
                        capability_code=authority.capability,
                        branch_id=authority_branch_id,
                        maximum_amount=authority.maximum_amount,
                        maximum_percentage=authority.maximum_percentage,
                        maker_checker_required=authority.maker_checker_required,
                    )
                )

        result = OrganizationBootstrapResponse(
            company=CompanyResponse(
                code=command.company.code,
                name=command.company.name,
                base_currency=command.company.base_currency,
                version=1,
            ),
            branches=branch_responses,
            configured_users=len(command.users),
        )
        await session.execute(
            insert(platform_command_receipts).values(
                command_id=uuid4(),
                idempotency_key=idempotency_key,
                actor_subject=actor.subject,
                request_hash=request_hash,
                response_json=result.model_dump(mode="json"),
            )
        )

    response.status_code = 201
    response.headers["X-Idempotency-Replayed"] = "false"
    return result
