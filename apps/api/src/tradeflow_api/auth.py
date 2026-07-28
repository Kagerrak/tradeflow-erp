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


async def require_organization_administrator(
    user: Annotated[AuthorizedUser, Depends(load_authorized_user)],
) -> AuthorizedUser:
    return require_capability(user, "organization:admin")
