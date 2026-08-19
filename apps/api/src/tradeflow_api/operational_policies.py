from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import AuthorizedUser, require_organization_administrator
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    branches,
    companies,
    document_series,
    document_templates,
)

router = APIRouter(prefix="/v1/organization", tags=["organization"])

_template_env = SandboxedEnvironment(undefined=StrictUndefined)


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentSeriesInput(CommandModel):
    prefix: str = Field(min_length=1, max_length=30)
    next_number: int = Field(ge=1, le=99999999)


class DocumentSeriesResponse(BaseModel):
    document_series_id: UUID
    branch_id: UUID
    document_type: str
    prefix: str
    next_number: int
    version: int


class DocumentTemplateInput(CommandModel):
    name: str = Field(min_length=1, max_length=200)
    template_body: str = Field(min_length=1, max_length=50000)
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def valid_effective_range(self) -> DocumentTemplateInput:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from.")
        return self


class DocumentTemplateResponse(BaseModel):
    document_template_id: UUID
    company_id: UUID
    branch_id: UUID | None
    document_type: str
    version: int
    name: str
    effective_from: date
    effective_to: date | None
    is_active: bool
    created_by: str
    created_at: str


class DocumentTemplatePreviewRequest(CommandModel):
    context: dict[str, Any] = Field(default_factory=dict)


class DocumentTemplatePreviewResponse(BaseModel):
    rendered_body: str


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


@router.put(
    "/branches/{branch_id}/document-series/{document_type}",
    response_model=DocumentSeriesResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def configure_branch_document_series(
    branch_id: UUID,
    document_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,38}$")],
    command: DocumentSeriesInput,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_organization_administrator)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    expected_version: Annotated[int | None, Header(alias="If-Match", ge=0)] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> DocumentSeriesResponse:
    expected_version, idempotency_key = require_command_headers(
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    request_hash = sha256(
        f"document-series:{branch_id}:{document_type}:{expected_version}:".encode()
        + command.model_dump_json().encode()
    ).hexdigest()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            await _require_branch_scope(session, actor=actor, branch_id=branch_id)
            response.headers["X-Idempotency-Replayed"] = "true"
            return DocumentSeriesResponse.model_validate(replay)

        await _require_branch_scope(session, actor=actor, branch_id=branch_id)
        existing = (
            await session.execute(
                select(
                    document_series.c.document_series_id,
                    document_series.c.prefix,
                    document_series.c.next_number,
                    document_series.c.version,
                )
                .where(
                    document_series.c.branch_id == branch_id,
                    document_series.c.document_type == document_type,
                )
                .with_for_update()
            )
        ).one_or_none()

        if existing is None:
            if expected_version != 0:
                raise AppError(
                    status_code=409,
                    code="optimistic_version_conflict",
                    message="The Document Series does not exist; create it with If-Match 0.",
                )
            series_id = uuid4()
            await session.execute(
                insert(document_series).values(
                    document_series_id=series_id,
                    branch_id=branch_id,
                    document_type=document_type,
                    prefix=command.prefix,
                    next_number=command.next_number,
                    version=1,
                )
            )
            response.status_code = 201
            result = DocumentSeriesResponse(
                document_series_id=series_id,
                branch_id=branch_id,
                document_type=document_type,
                prefix=command.prefix,
                next_number=command.next_number,
                version=1,
            )
        else:
            if existing.version != expected_version:
                raise AppError(
                    status_code=409,
                    code="optimistic_version_conflict",
                    message="The Document Series changed; reload it before retrying.",
                )
            _guard_series_number_regression(existing, command)
            new_version = existing.version + 1
            await session.execute(
                update(document_series)
                .where(document_series.c.document_series_id == existing.document_series_id)
                .values(
                    prefix=command.prefix,
                    next_number=command.next_number,
                    version=new_version,
                )
            )
            result = DocumentSeriesResponse(
                document_series_id=existing.document_series_id,
                branch_id=branch_id,
                document_type=document_type,
                prefix=command.prefix,
                next_number=command.next_number,
                version=new_version,
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


async def _require_branch_scope(
    session: AsyncSession, *, actor: AuthorizedUser, branch_id: UUID
) -> None:
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


def _guard_series_number_regression(existing: Any, command: DocumentSeriesInput) -> None:
    if command.next_number < existing.next_number:
        raise AppError(
            status_code=409,
            code="document_series_number_regression",
            message="Document Series next_number cannot be reduced; numbers must never be reused.",
        )


@router.get(
    "/branches/{branch_id}/document-series",
    response_model=list[DocumentSeriesResponse],
    responses=error_responses(400, 401, 403, 404, 500),
)
async def list_branch_document_series(
    branch_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_organization_administrator)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[DocumentSeriesResponse]:
    await _require_branch_scope(session, actor=actor, branch_id=branch_id)
    rows = (
        await session.execute(
            select(document_series).where(document_series.c.branch_id == branch_id)
        )
    ).mappings()
    return [DocumentSeriesResponse.model_validate(row) for row in rows]


@router.put(
    "/document-templates/{document_type}",
    response_model=DocumentTemplateResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def configure_company_document_template(
    document_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,38}$")],
    command: DocumentTemplateInput,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_organization_administrator)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> DocumentTemplateResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    async with session.begin():
        return await _create_template(
            session=session,
            actor=actor,
            document_type=document_type,
            command=command,
            idempotency_key=idempotency_key,
            response=response,
        )


@router.put(
    "/branches/{branch_id}/document-templates/{document_type}",
    response_model=DocumentTemplateResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def configure_branch_document_template(
    branch_id: UUID,
    document_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,38}$")],
    command: DocumentTemplateInput,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_organization_administrator)],
    session: Annotated[AsyncSession, Depends(get_database_session, use_cache=False)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> DocumentTemplateResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    async with session.begin():
        await _require_branch_scope(session, actor=actor, branch_id=branch_id)
        return await _create_template(
            session=session,
            actor=actor,
            document_type=document_type,
            command=command,
            idempotency_key=idempotency_key,
            response=response,
            branch_id=branch_id,
        )


async def _create_template(
    session: AsyncSession,
    actor: AuthorizedUser,
    document_type: str,
    command: DocumentTemplateInput,
    idempotency_key: str,
    response: Response,
    branch_id: UUID | None = None,
) -> DocumentTemplateResponse:
    request_hash = sha256(
        f"document-template:{document_type}:{branch_id or ''}:".encode()
        + command.model_dump_json().encode()
    ).hexdigest()
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.headers["X-Idempotency-Replayed"] = "true"
        return DocumentTemplateResponse.model_validate(replay)

    company = (await session.execute(select(companies.c.company_id))).one_or_none()
    if company is None:
        raise AppError(
            status_code=404,
            code="company_not_configured",
            message="TradeFlow has no configured Company.",
        )

    next_version_row = (
        await session.execute(
            select(func.coalesce(func.max(document_templates.c.version), 0) + 1).where(
                document_templates.c.company_id == company.company_id,
                document_templates.c.document_type == document_type,
                document_templates.c.branch_id == branch_id,
            )
        )
    ).scalar_one()
    next_version = int(next_version_row)

    template_id = uuid4()
    created_at = datetime.now(UTC)
    await session.execute(
        insert(document_templates).values(
            document_template_id=template_id,
            company_id=company.company_id,
            branch_id=branch_id,
            document_type=document_type,
            version=next_version,
            name=command.name,
            template_body=command.template_body,
            effective_from=command.effective_from,
            effective_to=command.effective_to,
            is_active=command.is_active,
            created_by=actor.subject,
            created_at=created_at,
        )
    )

    result = DocumentTemplateResponse(
        document_template_id=template_id,
        company_id=company.company_id,
        branch_id=branch_id,
        document_type=document_type,
        version=next_version,
        name=command.name,
        effective_from=command.effective_from,
        effective_to=command.effective_to,
        is_active=command.is_active,
        created_by=actor.subject,
        created_at=created_at.isoformat(),
    )
    await store_command_result(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result=result,
    )

    response.status_code = 201
    response.headers["X-Idempotency-Replayed"] = "false"
    return result


@router.get(
    "/document-templates/{document_type}",
    response_model=list[DocumentTemplateResponse],
    responses=error_responses(400, 401, 403, 500),
)
async def list_document_templates(
    document_type: str,
    actor: Annotated[AuthorizedUser, Depends(require_organization_administrator)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[DocumentTemplateResponse]:
    company = await session.scalar(select(companies.c.company_id))
    if company is None:
        raise AppError(
            status_code=404,
            code="company_not_configured",
            message="TradeFlow has no configured Company.",
        )
    rows = (
        await session.execute(
            select(document_templates)
            .where(
                document_templates.c.company_id == company,
                document_templates.c.document_type == document_type,
            )
            .order_by(document_templates.c.version.desc())
        )
    ).mappings()
    results = []
    for row in rows:
        payload = dict(row)
        payload["created_at"] = payload["created_at"].isoformat()
        results.append(DocumentTemplateResponse.model_validate(payload))
    return results


@router.post(
    "/document-templates/{document_template_id}/preview",
    response_model=DocumentTemplatePreviewResponse,
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
async def preview_document_template(
    document_template_id: UUID,
    command: DocumentTemplatePreviewRequest,
    actor: Annotated[AuthorizedUser, Depends(require_organization_administrator)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> DocumentTemplatePreviewResponse:
    row = (
        await session.execute(
            select(
                document_templates.c.template_body,
                document_templates.c.branch_id,
            ).where(document_templates.c.document_template_id == document_template_id)
        )
    ).one_or_none()
    if row is None:
        raise AppError(
            status_code=404,
            code="document_template_not_found",
            message="The Document Template does not exist.",
        )
    if row.branch_id is not None and row.branch_id not in actor.branch_ids:
        raise AppError(
            status_code=403,
            code="operational_scope_required",
            message="The Document Template is outside the user's Operational Scope.",
        )

    rendered = _render_template(row.template_body, command.context)
    return DocumentTemplatePreviewResponse(rendered_body=rendered)


def _render_template(template_body: str, context: dict[str, Any]) -> str:
    try:
        template = _template_env.from_string(template_body)
        sorted_context = cast(dict[str, Any], _sort_context(context))
        return template.render(sorted_context)
    except Exception as error:
        raise AppError(
            status_code=422,
            code="template_render_failed",
            message=f"Could not render template: {error}",
        ) from error


def _sort_context(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_context(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_context(item) for item in value]
    return value
