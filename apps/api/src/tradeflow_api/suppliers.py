from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_supplier_reader,
    require_supplier_writer,
)
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import companies, suppliers

router = APIRouter(prefix="/v1/procurement/suppliers", tags=["procurement"])


class SupplierCommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSupplierCommand(SupplierCommandModel):
    code: str = Field(min_length=1, max_length=50)
    legal_name: str = Field(min_length=1, max_length=200)
    tax_id: str | None = Field(default=None, max_length=50)
    payment_terms: str = Field(min_length=1, max_length=50)
    default_currency: str = Field(pattern=r"^[A-Z]{3}$")


class SupplierResponse(BaseModel):
    supplier_id: UUID
    code: str
    legal_name: str
    tax_id: str | None
    payment_terms: str
    default_currency: str
    is_active: bool
    version: int


class SupplierSearchItem(BaseModel):
    supplier_id: UUID
    code: str
    legal_name: str
    tax_id: str | None
    default_currency: str
    is_active: bool
    version: int


class SupplierSearchResponse(BaseModel):
    items: list[SupplierSearchItem]
    total: int


async def _company_id(session: AsyncSession) -> UUID:
    company_id = await session.scalar(select(companies.c.company_id).limit(1))
    if company_id is None:
        raise AppError(500, "company_missing", "Company not configured.")
    return cast(UUID, company_id)


@router.post(
    "",
    response_model=SupplierResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 409, 503),
)
async def create_supplier(
    command: CreateSupplierCommand,
    actor: Annotated[AuthorizedUser, Depends(require_supplier_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> SupplierResponse:
    company_id = await _company_id(session)

    existing = await session.scalar(
        select(suppliers.c.supplier_id).where(
            suppliers.c.company_id == company_id,
            suppliers.c.code == command.code,
        )
    )
    if existing is not None:
        raise AppError(
            409,
            "supplier_code_duplicate",
            "A supplier with this code already exists.",
        )

    supplier_id = uuid4()
    await session.rollback()
    async with session.begin():
        await session.execute(
            insert(suppliers).values(
                supplier_id=supplier_id,
                company_id=company_id,
                code=command.code,
                legal_name=command.legal_name,
                tax_id=command.tax_id,
                payment_terms=command.payment_terms,
                default_currency=command.default_currency,
                is_active=True,
                version=1,
                created_by=actor.subject,
            )
        )

    return SupplierResponse(
        supplier_id=supplier_id,
        code=command.code,
        legal_name=command.legal_name,
        tax_id=command.tax_id,
        payment_terms=command.payment_terms,
        default_currency=command.default_currency,
        is_active=True,
        version=1,
    )


@router.get(
    "",
    response_model=SupplierSearchResponse,
    responses=error_responses(401, 403, 503),
)
async def list_suppliers(
    actor: Annotated[AuthorizedUser, Depends(require_supplier_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    query: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SupplierSearchResponse:
    company_id = await _company_id(session)

    filters = [suppliers.c.company_id == company_id]
    if query.strip():
        term = f"%{query.strip()}%"
        filters.append(
            or_(
                suppliers.c.code.ilike(term),
                suppliers.c.legal_name.ilike(term),
            )
        )

    total = await session.scalar(select(func.count(suppliers.c.supplier_id)).where(*filters))

    rows = (
        (
            await session.execute(
                select(
                    suppliers.c.supplier_id,
                    suppliers.c.code,
                    suppliers.c.legal_name,
                    suppliers.c.tax_id,
                    suppliers.c.default_currency,
                    suppliers.c.is_active,
                    suppliers.c.version,
                )
                .where(*filters)
                .order_by(suppliers.c.code)
                .limit(limit)
                .offset(offset)
            )
        )
        .mappings()
        .all()
    )

    return SupplierSearchResponse(
        items=[
            SupplierSearchItem(
                supplier_id=row["supplier_id"],
                code=row["code"],
                legal_name=row["legal_name"],
                tax_id=row["tax_id"],
                default_currency=row["default_currency"],
                is_active=row["is_active"],
                version=row["version"],
            )
            for row in rows
        ],
        total=total or 0,
    )
