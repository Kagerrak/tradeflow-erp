from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {
            "description": "Stable TradeFlow error envelope.",
            "model": ErrorEnvelope,
        }
        for status_code in status_codes
    }


@dataclass(frozen=True, slots=True)
class AppError(Exception):
    status_code: int
    code: str
    message: str


def error_response(request: Request, error: AppError) -> JSONResponse:
    correlation_id = request.state.correlation_id
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "correlation_id": correlation_id,
            }
        },
        headers={"X-Correlation-ID": correlation_id},
    )
