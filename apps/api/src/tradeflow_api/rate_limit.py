from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from tradeflow_api.config import Settings
from tradeflow_api.errors import AppError, error_response

EXCLUDED_PATHS = {"/health/live", "/health/ready"}


@dataclass
class _RateLimitWindow:
    requests: deque[float] = field(default_factory=deque)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self._requests_per_minute = requests_per_minute
        self._window_seconds = 60.0
        self._buckets: dict[str, _RateLimitWindow] = {}
        self._cleanup_lock = asyncio.Lock()

    def _key(self, request: Request) -> str:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            return f"token:{auth_header[7:].strip()}"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            peer = forwarded.split(",")[0].strip()
        elif request.client is not None:
            peer = request.client.host
        else:
            peer = "unknown"
        return f"ip:{peer}"

    async def is_allowed(self, request: Request) -> tuple[bool, float]:
        if self._requests_per_minute <= 0:
            return True, 0.0
        key = self._key(request)
        window = self._buckets.setdefault(key, _RateLimitWindow())
        now = time.monotonic()
        async with window.lock:
            cutoff = now - self._window_seconds
            while window.requests and window.requests[0] <= cutoff:
                window.requests.popleft()
            if len(window.requests) >= self._requests_per_minute:
                retry_after = int(self._window_seconds - (now - window.requests[0])) + 1
                return False, max(retry_after, 1)
            window.requests.append(now)
            return True, 0.0

    async def cleanup(self) -> None:
        async with self._cleanup_lock:
            cutoff = time.monotonic() - self._window_seconds
            empty_keys = [
                key
                for key, window in self._buckets.items()
                if not any(timestamp > cutoff for timestamp in window.requests)
            ]
            for key in empty_keys:
                del self._buckets[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self._enabled = settings.rate_limit_enabled
        self._limiter = RateLimiter(settings.rate_limit_requests_per_minute)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not self._enabled or request.url.path in EXCLUDED_PATHS:
            return await call_next(request)
        allowed, retry_after = await self._limiter.is_allowed(request)
        if not allowed:
            return error_response(
                request,
                AppError(
                    status_code=429,
                    code="rate_limit_exceeded",
                    message="Rate limit exceeded. Please retry later.",
                ),
                extra_headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
