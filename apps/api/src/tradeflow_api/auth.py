from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from tradeflow_api.config import Settings
from tradeflow_api.errors import AppError

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    subject: str
    display_name: str
    capabilities: tuple[str, ...]


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
