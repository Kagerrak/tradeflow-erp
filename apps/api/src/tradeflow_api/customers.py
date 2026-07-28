from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tradeflow_api.auth import (
    AuthorizedUser,
    require_customer_credit_approver,
    require_customer_reader,
    require_customer_writer,
)
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    branches,
    customer_accounts,
    customer_address_versions,
    customer_contacts,
    customer_credit_approvals,
    platform_command_receipts,
)

router = APIRouter(prefix="/v1/customers", tags=["customers"])


class CustomerCommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactInput(CustomerCommandModel):
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def require_contact_channel(self) -> ContactInput:
        if self.email is None and self.phone is None:
            raise ValueError("A Contact requires an email address or phone number.")
        return self


class AddressInput(CustomerCommandModel):
    address_key: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,49}$")
    kind: Literal["billing", "delivery"]
    line_1: str = Field(min_length=1, max_length=200)
    line_2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=30)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")


class UpdateAddressCommand(CustomerCommandModel):
    kind: Literal["billing", "delivery"]
    line_1: str = Field(min_length=1, max_length=200)
    line_2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=30)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")


class CreateCustomerCommand(CustomerCommandModel):
    account_number: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{1,49}$")
    branch_id: UUID
    legal_name: str = Field(min_length=1, max_length=200)
    status: Literal["active", "inactive", "prospect"]
    payment_terms: str = Field(min_length=1, max_length=50)
    payment_timing_policy: Literal[
        "prepaid",
        "cash_on_delivery",
        "on_account",
    ]
    credit_limit: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=18,
        decimal_places=2,
    )
    credit_hold: bool = False
    contacts: list[ContactInput] = Field(default_factory=list)
    addresses: list[AddressInput] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_address_keys(self) -> CreateCustomerCommand:
        keys = [address.address_key for address in self.addresses]
        if len(keys) != len(set(keys)):
            raise ValueError("Customer Address keys must be unique.")
        return self


class ContactResponse(BaseModel):
    contact_id: UUID
    name: str
    role: str
    email: str | None
    phone: str | None
    is_active: bool
    version: int


class AddressResponse(BaseModel):
    address_version_id: UUID
    address_key: str
    version: int
    kind: Literal["billing", "delivery"]
    line_1: str
    line_2: str | None
    city: str
    region: str
    postal_code: str
    country_code: str
    is_current: bool


class CustomerResponse(BaseModel):
    customer_id: UUID
    account_number: str
    branch_id: UUID
    legal_name: str
    status: Literal["active", "inactive", "prospect"]
    payment_terms: str
    payment_timing_policy: Literal[
        "prepaid",
        "cash_on_delivery",
        "on_account",
    ]
    credit_limit: Decimal | None
    credit_hold: bool
    version: int
    contacts: list[ContactResponse]
    addresses: list[AddressResponse]


class CustomerSearchItem(BaseModel):
    customer_id: UUID
    account_number: str
    branch_id: UUID
    legal_name: str
    status: Literal["active", "inactive", "prospect"]
    payment_timing_policy: Literal[
        "prepaid",
        "cash_on_delivery",
        "on_account",
    ]
    credit_hold: bool
    version: int


class CustomerSearchResponse(BaseModel):
    items: list[CustomerSearchItem]
    total: int


class AddressUpdateResponse(BaseModel):
    customer_id: UUID
    customer_version: int
    address: AddressResponse


class CreditApprovalCommand(CustomerCommandModel):
    reason: str = Field(min_length=1, max_length=500)


class CreditApprovalResponse(BaseModel):
    credit_approval_id: UUID
    customer_id: UUID
    approved_by: str
    customer_version: int
    credit_limit: Decimal
    credit_hold: bool
    reason: str


@router.get(
    "",
    response_model=CustomerSearchResponse,
    responses=error_responses(401, 403, 422, 500),
)
async def search_customers(
    actor: Annotated[AuthorizedUser, Depends(require_customer_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    query: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> CustomerSearchResponse:
    if not actor.branch_ids:
        return CustomerSearchResponse(items=[], total=0)

    filters: list[ColumnElement[bool]] = [customer_accounts.c.branch_id.in_(actor.branch_ids)]
    if query.strip():
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                customer_accounts.c.account_number.ilike(pattern),
                customer_accounts.c.legal_name.ilike(pattern),
            )
        )
    total = await session.scalar(
        select(func.count()).select_from(customer_accounts).where(*filters)
    )
    rows = (
        await session.execute(
            select(
                customer_accounts.c.customer_id,
                customer_accounts.c.account_number,
                customer_accounts.c.branch_id,
                customer_accounts.c.legal_name,
                customer_accounts.c.status,
                customer_accounts.c.payment_timing_policy,
                customer_accounts.c.credit_hold,
                customer_accounts.c.version,
            )
            .where(*filters)
            .order_by(
                customer_accounts.c.legal_name,
                customer_accounts.c.account_number,
            )
            .limit(limit)
        )
    ).mappings()
    return CustomerSearchResponse(
        items=[CustomerSearchItem.model_validate(row) for row in rows],
        total=total or 0,
    )


@router.get(
    "/{customer_id}/addresses/{address_key}/versions/{version}",
    response_model=AddressResponse,
    responses=error_responses(401, 403, 404, 422, 500),
)
async def get_customer_address_version(
    customer_id: UUID,
    address_key: str,
    version: int,
    actor: Annotated[AuthorizedUser, Depends(require_customer_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AddressResponse:
    row = (
        (
            await session.execute(
                select(
                    customer_address_versions.c.address_version_id,
                    customer_address_versions.c.address_key,
                    customer_address_versions.c.version,
                    customer_address_versions.c.kind,
                    customer_address_versions.c.line_1,
                    customer_address_versions.c.line_2,
                    customer_address_versions.c.city,
                    customer_address_versions.c.region,
                    customer_address_versions.c.postal_code,
                    customer_address_versions.c.country_code,
                    customer_address_versions.c.is_current,
                    customer_accounts.c.branch_id,
                )
                .select_from(
                    customer_address_versions.join(
                        customer_accounts,
                        customer_address_versions.c.customer_id == customer_accounts.c.customer_id,
                    )
                )
                .where(
                    customer_address_versions.c.customer_id == customer_id,
                    customer_address_versions.c.address_key == address_key,
                    customer_address_versions.c.version == version,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(
            status_code=404,
            code="customer_address_version_not_found",
            message="The requested Customer Address version does not exist.",
        )
    if row["branch_id"] not in actor.branch_ids:
        raise AppError(
            status_code=403,
            code="operational_scope_required",
            message="The Customer Account is outside the user's Branch scope.",
        )
    return AddressResponse.model_validate(row)


@router.put(
    "/{customer_id}/addresses/{address_key}",
    response_model=AddressUpdateResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def update_customer_address(
    customer_id: UUID,
    address_key: str,
    command: UpdateAddressCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_customer_writer)],
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
) -> AddressUpdateResponse:
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

    request_hash = sha256(
        f"{customer_id}:{address_key}:{expected_version}:".encode()
        + command.model_dump_json().encode()
    ).hexdigest()
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
            current_branch_id = await session.scalar(
                select(customer_accounts.c.branch_id).where(
                    customer_accounts.c.customer_id == customer_id
                )
            )
            if current_branch_id is None:
                raise AppError(
                    status_code=404,
                    code="customer_not_found",
                    message="The Customer Account does not exist.",
                )
            if current_branch_id not in actor.branch_ids:
                raise AppError(
                    status_code=403,
                    code="operational_scope_required",
                    message="The Customer Account is outside the user's Branch scope.",
                )
            response.headers["X-Idempotency-Replayed"] = "true"
            return AddressUpdateResponse.model_validate(stored_response)

        customer = (
            await session.execute(
                select(
                    customer_accounts.c.branch_id,
                    customer_accounts.c.version,
                )
                .where(customer_accounts.c.customer_id == customer_id)
                .with_for_update()
            )
        ).one_or_none()
        if customer is None:
            raise AppError(
                status_code=404,
                code="customer_not_found",
                message="The Customer Account does not exist.",
            )
        if customer.branch_id not in actor.branch_ids:
            raise AppError(
                status_code=403,
                code="operational_scope_required",
                message="The Customer Account is outside the user's Branch scope.",
            )
        if customer.version != expected_version:
            raise AppError(
                status_code=409,
                code="optimistic_version_conflict",
                message="The Customer Account changed; reload it before retrying.",
            )

        current_address = (
            await session.execute(
                select(customer_address_versions.c.version)
                .where(
                    customer_address_versions.c.customer_id == customer_id,
                    customer_address_versions.c.address_key == address_key,
                    customer_address_versions.c.is_current.is_(True),
                )
                .with_for_update()
            )
        ).one_or_none()
        if current_address is None:
            raise AppError(
                status_code=404,
                code="customer_address_not_found",
                message="The Customer Address does not exist.",
            )

        await session.execute(
            update(customer_address_versions)
            .where(
                customer_address_versions.c.customer_id == customer_id,
                customer_address_versions.c.address_key == address_key,
                customer_address_versions.c.is_current.is_(True),
            )
            .values(is_current=False)
        )
        address_version = current_address.version + 1
        address_version_id = uuid4()
        await session.execute(
            insert(customer_address_versions).values(
                address_version_id=address_version_id,
                customer_id=customer_id,
                address_key=address_key,
                version=address_version,
                kind=command.kind,
                line_1=command.line_1,
                line_2=command.line_2,
                city=command.city,
                region=command.region,
                postal_code=command.postal_code,
                country_code=command.country_code,
                is_current=True,
                created_by=actor.subject,
            )
        )
        customer_version = customer.version + 1
        await session.execute(
            update(customer_accounts)
            .where(customer_accounts.c.customer_id == customer_id)
            .values(
                version=customer_version,
                updated_at=func.now(),
            )
        )
        result = AddressUpdateResponse(
            customer_id=customer_id,
            customer_version=customer_version,
            address=AddressResponse(
                address_version_id=address_version_id,
                address_key=address_key,
                version=address_version,
                kind=command.kind,
                line_1=command.line_1,
                line_2=command.line_2,
                city=command.city,
                region=command.region,
                postal_code=command.postal_code,
                country_code=command.country_code,
                is_current=True,
            ),
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

    response.headers["X-Idempotency-Replayed"] = "false"
    return result


@router.post(
    "/{customer_id}/credit-approvals",
    response_model=CreditApprovalResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
    status_code=201,
)
async def approve_customer_credit(
    customer_id: UUID,
    command: CreditApprovalCommand,
    response: Response,
    actor: Annotated[
        AuthorizedUser,
        Depends(require_customer_credit_approver),
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
) -> CreditApprovalResponse:
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

    request_hash = sha256(
        f"{customer_id}:{expected_version}:".encode() + command.model_dump_json().encode()
    ).hexdigest()
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
            current_customer = (
                await session.execute(
                    select(
                        customer_accounts.c.branch_id,
                        customer_accounts.c.created_by,
                        customer_accounts.c.credit_limit,
                    ).where(customer_accounts.c.customer_id == customer_id)
                )
            ).one_or_none()
            if current_customer is None:
                raise AppError(
                    status_code=404,
                    code="customer_not_found",
                    message="The Customer Account does not exist.",
                )
            if current_customer.branch_id not in actor.branch_ids:
                raise AppError(
                    status_code=403,
                    code="operational_scope_required",
                    message="The Customer Account is outside the user's Branch scope.",
                )
            current_authority = (
                await session.execute(
                    select(
                        approval_authorities.c.maximum_amount,
                        approval_authorities.c.maker_checker_required,
                    ).where(
                        approval_authorities.c.user_subject == actor.subject,
                        approval_authorities.c.capability_code == "customer:credit-approve",
                        approval_authorities.c.branch_id == current_customer.branch_id,
                    )
                )
            ).one_or_none()
            if current_authority is None:
                raise AppError(
                    status_code=403,
                    code="approval_authority_required",
                    message="Explicit Customer credit Approval Authority is required.",
                )
            current_limit = current_customer.credit_limit or Decimal("0.00")
            if (
                current_authority.maximum_amount is not None
                and current_limit > current_authority.maximum_amount
            ):
                raise AppError(
                    status_code=403,
                    code="approval_limit_exceeded",
                    message="The Customer credit limit exceeds Approval Authority.",
                )
            if (
                current_authority.maker_checker_required
                and current_customer.created_by == actor.subject
            ):
                raise AppError(
                    status_code=409,
                    code="maker_checker_violation",
                    message="The Customer maker cannot approve the same credit decision.",
                )
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return CreditApprovalResponse.model_validate(stored_response)

        customer = (
            await session.execute(
                select(
                    customer_accounts.c.branch_id,
                    customer_accounts.c.created_by,
                    customer_accounts.c.credit_limit,
                    customer_accounts.c.version,
                )
                .where(customer_accounts.c.customer_id == customer_id)
                .with_for_update()
            )
        ).one_or_none()
        if customer is None:
            raise AppError(
                status_code=404,
                code="customer_not_found",
                message="The Customer Account does not exist.",
            )
        if customer.branch_id not in actor.branch_ids:
            raise AppError(
                status_code=403,
                code="operational_scope_required",
                message="The Customer Account is outside the user's Branch scope.",
            )
        if customer.version != expected_version:
            raise AppError(
                status_code=409,
                code="optimistic_version_conflict",
                message="The Customer Account changed; reload it before retrying.",
            )

        authority = (
            await session.execute(
                select(
                    approval_authorities.c.maximum_amount,
                    approval_authorities.c.maker_checker_required,
                ).where(
                    approval_authorities.c.user_subject == actor.subject,
                    approval_authorities.c.capability_code == "customer:credit-approve",
                    approval_authorities.c.branch_id == customer.branch_id,
                )
            )
        ).one_or_none()
        if authority is None:
            raise AppError(
                status_code=403,
                code="approval_authority_required",
                message="Explicit Customer credit Approval Authority is required.",
            )

        credit_limit = customer.credit_limit or Decimal("0.00")
        if authority.maximum_amount is not None and credit_limit > authority.maximum_amount:
            raise AppError(
                status_code=403,
                code="approval_limit_exceeded",
                message="The Customer credit limit exceeds Approval Authority.",
            )
        if authority.maker_checker_required and customer.created_by == actor.subject:
            raise AppError(
                status_code=409,
                code="maker_checker_violation",
                message="The Customer maker cannot approve the same credit decision.",
            )

        customer_version = customer.version + 1
        credit_approval_id = uuid4()
        await session.execute(
            insert(customer_credit_approvals).values(
                credit_approval_id=credit_approval_id,
                customer_id=customer_id,
                approved_by=actor.subject,
                maker_subject=customer.created_by,
                approved_limit=credit_limit,
                reason=command.reason,
            )
        )
        await session.execute(
            update(customer_accounts)
            .where(customer_accounts.c.customer_id == customer_id)
            .values(
                credit_hold=False,
                version=customer_version,
                updated_at=func.now(),
            )
        )
        result = CreditApprovalResponse(
            credit_approval_id=credit_approval_id,
            customer_id=customer_id,
            approved_by=actor.subject,
            customer_version=customer_version,
            credit_limit=credit_limit,
            credit_hold=False,
            reason=command.reason,
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


@router.post(
    "",
    response_model=CustomerResponse,
    responses=error_responses(400, 401, 403, 409, 422, 500),
    status_code=201,
)
async def create_customer(
    command: CreateCustomerCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_customer_writer)],
    session: Annotated[
        AsyncSession,
        Depends(get_database_session, use_cache=False),
    ],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CustomerResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )
    if command.branch_id not in actor.branch_ids:
        raise AppError(
            status_code=403,
            code="operational_scope_required",
            message="The Customer Account is outside the user's Branch scope.",
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
            return CustomerResponse.model_validate(stored_response)

        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:account_identity))"),
            {"account_identity": f"customer:{command.account_number}"},
        )
        active_branch = await session.scalar(
            select(branches.c.branch_id).where(
                branches.c.branch_id == command.branch_id,
                branches.c.is_active.is_(True),
            )
        )
        if active_branch is None:
            raise AppError(
                status_code=409,
                code="branch_inactive",
                message="Customer Accounts can only be created in an active Branch.",
            )
        existing_customer = await session.scalar(
            select(customer_accounts.c.customer_id).where(
                customer_accounts.c.account_number == command.account_number
            )
        )
        if existing_customer is not None:
            raise AppError(
                status_code=409,
                code="customer_account_number_exists",
                message="The Customer Account number is already in use.",
            )

        customer_id = uuid4()
        await session.execute(
            insert(customer_accounts).values(
                customer_id=customer_id,
                branch_id=command.branch_id,
                account_number=command.account_number,
                legal_name=command.legal_name,
                status=command.status,
                payment_terms=command.payment_terms,
                payment_timing_policy=command.payment_timing_policy,
                credit_limit=command.credit_limit,
                credit_hold=command.credit_hold,
                created_by=actor.subject,
            )
        )

        contact_responses: list[ContactResponse] = []
        for contact in command.contacts:
            contact_id = uuid4()
            await session.execute(
                insert(customer_contacts).values(
                    contact_id=contact_id,
                    customer_id=customer_id,
                    name=contact.name,
                    role=contact.role,
                    email=contact.email,
                    phone=contact.phone,
                )
            )
            contact_responses.append(
                ContactResponse(
                    contact_id=contact_id,
                    name=contact.name,
                    role=contact.role,
                    email=contact.email,
                    phone=contact.phone,
                    is_active=True,
                    version=1,
                )
            )

        address_responses: list[AddressResponse] = []
        for address in command.addresses:
            address_version_id = uuid4()
            await session.execute(
                insert(customer_address_versions).values(
                    address_version_id=address_version_id,
                    customer_id=customer_id,
                    address_key=address.address_key,
                    version=1,
                    kind=address.kind,
                    line_1=address.line_1,
                    line_2=address.line_2,
                    city=address.city,
                    region=address.region,
                    postal_code=address.postal_code,
                    country_code=address.country_code,
                    is_current=True,
                    created_by=actor.subject,
                )
            )
            address_responses.append(
                AddressResponse(
                    address_version_id=address_version_id,
                    address_key=address.address_key,
                    version=1,
                    kind=address.kind,
                    line_1=address.line_1,
                    line_2=address.line_2,
                    city=address.city,
                    region=address.region,
                    postal_code=address.postal_code,
                    country_code=address.country_code,
                    is_current=True,
                )
            )

        result = CustomerResponse(
            customer_id=customer_id,
            account_number=command.account_number,
            branch_id=command.branch_id,
            legal_name=command.legal_name,
            status=command.status,
            payment_terms=command.payment_terms,
            payment_timing_policy=command.payment_timing_policy,
            credit_limit=command.credit_limit,
            credit_hold=command.credit_hold,
            version=1,
            contacts=contact_responses,
            addresses=address_responses,
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
