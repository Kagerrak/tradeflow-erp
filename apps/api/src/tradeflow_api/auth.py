from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.config import Settings
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError
from tradeflow_api.models import (
    role_template_capabilities,
    role_templates,
    user_branch_scopes,
    user_role_templates,
    user_warehouse_scopes,
    users,
)

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    subject: str
    display_name: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorizedUser:
    subject: str
    display_name: str
    capabilities: tuple[str, ...]
    branch_ids: tuple[UUID, ...]
    warehouse_ids: tuple[UUID, ...]
    is_operations_administrator: bool


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks = (
            PyJWKClient(settings.auth_jwks_url) if settings.auth_jwks_url is not None else None
        )

    def verify(self, token: str) -> CurrentUser:
        try:
            claims = self._decode(token)
            subject = str(claims["sub"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
            raise AppError(
                status_code=401,
                code="invalid_token",
                message="The bearer token is invalid or expired.",
            ) from error

        raw_capabilities = claims.get("capabilities", [])
        if not isinstance(raw_capabilities, list) or not all(
            isinstance(value, str) for value in raw_capabilities
        ):
            raise AppError(
                status_code=401,
                code="invalid_token",
                message="The bearer token is invalid or expired.",
            )

        return CurrentUser(
            subject=subject,
            display_name=str(claims.get("name", subject)),
            capabilities=tuple(sorted(set(raw_capabilities))),
        )

    def _decode(self, token: str) -> dict[str, Any]:
        if self._settings.auth_test_secret is not None:
            return jwt.decode(
                token,
                self._settings.auth_test_secret,
                algorithms=["HS256"],
                audience=self._settings.auth_audience,
                issuer=self._settings.auth_issuer,
                options={"require": ["aud", "exp", "iss", "sub"]},
            )

        if self._jwks is None:
            raise ValueError("No token verifier is configured.")

        signing_key = self._jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=self._settings.auth_audience,
            issuer=self._settings.auth_issuer,
            options={"require": ["aud", "exp", "iss", "sub"]},
        )


async def authenticate(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            status_code=401,
            code="authentication_required",
            message="A valid bearer token is required.",
        )
    verifier: TokenVerifier = request.app.state.token_verifier
    return verifier.verify(credentials.credentials)


async def require_platform_reader(
    user: Annotated[CurrentUser, Depends(authenticate)],
) -> CurrentUser:
    capability = "platform:read"
    if capability not in user.capabilities:
        raise AppError(
            status_code=403,
            code="capability_required",
            message=f"The '{capability}' capability is required.",
        )
    return user


async def require_platform_writer(
    user: Annotated[CurrentUser, Depends(authenticate)],
) -> CurrentUser:
    capability = "platform:write"
    if capability not in user.capabilities:
        raise AppError(
            status_code=403,
            code="capability_required",
            message=f"The '{capability}' capability is required.",
        )
    return user


async def require_organization_bootstrapper(
    user: Annotated[CurrentUser, Depends(authenticate)],
) -> CurrentUser:
    capability = "organization:bootstrap"
    if capability not in user.capabilities:
        raise AppError(
            status_code=403,
            code="capability_required",
            message=f"The '{capability}' capability is required.",
        )
    return user


async def load_authorized_user(
    identity: Annotated[CurrentUser, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AuthorizedUser:
    configured_user = (
        await session.execute(
            select(
                users.c.display_name,
                users.c.is_operations_administrator,
                users.c.is_active,
            ).where(users.c.subject == identity.subject)
        )
    ).one_or_none()
    if configured_user is None or not configured_user.is_active:
        raise AppError(
            status_code=403,
            code="operational_access_required",
            message="The user has no active TradeFlow operational assignment.",
        )

    capability_codes = (
        await session.execute(
            select(role_template_capabilities.c.capability_code)
            .select_from(
                user_role_templates.join(
                    role_templates,
                    user_role_templates.c.role_template_id == role_templates.c.role_template_id,
                ).join(
                    role_template_capabilities,
                    role_templates.c.role_template_id
                    == role_template_capabilities.c.role_template_id,
                )
            )
            .where(
                user_role_templates.c.user_subject == identity.subject,
                role_templates.c.is_active.is_(True),
            )
            .distinct()
        )
    ).scalars()
    branch_ids = (
        await session.execute(
            select(user_branch_scopes.c.branch_id).where(
                user_branch_scopes.c.user_subject == identity.subject
            )
        )
    ).scalars()
    warehouse_ids = (
        await session.execute(
            select(user_warehouse_scopes.c.warehouse_id).where(
                user_warehouse_scopes.c.user_subject == identity.subject
            )
        )
    ).scalars()

    return AuthorizedUser(
        subject=identity.subject,
        display_name=configured_user.display_name,
        capabilities=tuple(sorted(set(capability_codes))),
        branch_ids=tuple(sorted(set(branch_ids), key=str)),
        warehouse_ids=tuple(sorted(set(warehouse_ids), key=str)),
        is_operations_administrator=configured_user.is_operations_administrator,
    )


def require_capability(
    user: AuthorizedUser,
    capability: str,
) -> AuthorizedUser:
    if capability not in user.capabilities:
        raise AppError(
            status_code=403,
            code="capability_required",
            message=f"The '{capability}' capability is required.",
        )
    return user


async def require_customer_writer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "customer:write")


async def require_customer_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "customer:read")


async def require_customer_credit_approver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "customer:credit-approve")


async def require_supplier_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:supplier-read")


async def require_supplier_writer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:supplier-write")


async def require_purchase_order_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:purchase-order-read")


async def require_purchase_order_writer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:purchase-order-write")


async def require_purchase_order_approver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:purchase-order-approve")


async def require_purchase_request_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:purchase-request-read")


async def require_purchase_request_writer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:purchase-request-write")


async def require_purchase_request_approver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:purchase-request-approve")


async def require_goods_receipt_poster(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:goods-receipt-post")


async def require_goods_receipt_over_receipt_approver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:goods-receipt-approve-over-receipt")


async def require_landed_cost_allocator(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "procurement:landed-cost-allocate")


async def require_catalog_writer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "catalog:write")


async def require_inventory_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:read")


async def require_inventory_poster(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:post")


async def require_inventory_rebuilder(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:rebuild")


async def require_inventory_transfer_requester(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:transfer-request")


async def require_inventory_transfer_receiver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:transfer-receive")


async def require_inventory_adjustment_requester(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:adjustment-request")


async def require_inventory_adjustment_approver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:adjustment-approve")


async def require_sales_order_writer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:order-write")


async def require_sales_order_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:order-read")


async def require_sales_pricing_writer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:pricing-write")


async def require_commercial_approver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:commercial-approve")


async def require_order_canceller(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:order-cancel")


async def require_sales_projection_rebuilder(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:projection-rebuild")


async def require_quotation_writer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:quotation-write")


async def require_quotation_approver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:quotation-approve")


async def require_quotation_converter(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:quotation-convert")


async def require_payment_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:payment-read")


async def require_payment_recorder(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:payment-record")


async def require_payment_verifier(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:payment-verify")


async def require_check_clearer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:check-clear")


async def require_payment_reverser(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:payment-reverse")


async def require_payment_refunder(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:payment-refund")


async def require_cash_reconciler(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:cash-reconcile")


async def require_cod_on_account_converter(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "sales:cod-convert-on-account")


async def require_payment_projection_rebuilder(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:projection-rebuild")


async def require_invoice_poster(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:invoice-post")


async def require_invoice_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:invoice-read")


async def require_invoice_voider(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:invoice-void")


async def require_credit_note_requester(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:credit-note-request")


async def require_credit_note_approver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:credit-note-approve")


async def require_credit_note_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:credit-note-read")


async def require_expense_category_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:expense-category-read")


async def require_expense_category_creator(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:expense-category-create")


async def require_expense_category_publisher(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:expense-category-publish")


async def require_expense_policy_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:expense-policy-read")


async def require_expense_policy_creator(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:expense-policy-create")


async def require_expense_policy_publisher(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:expense-policy-publish")


async def require_payment_allocator(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:payment-allocate")


async def require_statement_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "finance:statement-read")


async def require_pick_releaser(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:pick-release")


async def require_picker(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:pick")


async def require_pick_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:pick-read")


async def require_pick_reverser(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:pick-reverse")


async def require_dispatcher(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:dispatch")


async def require_delivery_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:delivery-read")


async def require_delivery_confirmer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:delivery-confirm")


async def require_delivery_receipt_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    if not {
        "fulfillment:delivery-confirm",
        "fulfillment:delivery-correction-request",
        "fulfillment:delivery-correction-authorize",
        "returns:request",
        "returns:authorize",
    }.intersection(user.capabilities):
        raise AppError(
            status_code=403,
            code="capability_required",
            message="A Delivery Receipt, Delivery Correction, or Returns capability is required.",
        )
    return user


async def require_delivery_correction_requester(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:delivery-correction-request")


async def require_delivery_correction_authorizer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:delivery-correction-authorize")


async def require_delivery_correction_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    if not {
        "fulfillment:delivery-correction-request",
        "fulfillment:delivery-correction-authorize",
    }.intersection(user.capabilities):
        raise AppError(
            status_code=403,
            code="capability_required",
            message="A Delivery Correction capability is required.",
        )
    return user


async def require_return_requester(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "returns:request")


async def require_return_authorizer(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "returns:authorize")


async def require_return_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    if not {"returns:request", "returns:authorize", "returns:receive"}.intersection(
        user.capabilities
    ):
        raise AppError(
            status_code=403,
            code="capability_required",
            message="A Returns capability is required.",
        )
    return user


async def require_return_receiver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "returns:receive")


async def require_delivery_exception_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:delivery-read")


async def require_return_to_warehouse_receiver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:return-receive")


async def require_delivery_retrier(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "fulfillment:delivery-retry")


async def require_investigation_resolver(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:investigation-resolve")


async def require_reservation_retrier(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:reservation-retry")


async def require_payment_deadline_processor(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "inventory:payment-deadline-process")


async def require_organization_administrator(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "organization:admin")


async def require_notification_manager(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "notification:manage")


async def require_notification_reader(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "notification:read")
