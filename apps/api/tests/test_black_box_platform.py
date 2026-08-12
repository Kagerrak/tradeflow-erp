from __future__ import annotations

import os
from uuid import UUID, uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TRADEFLOW_REAL_STACK") != "1",
    reason="Runs only against the migrated real-stack acceptance environment.",
)


def test_external_api_uses_the_generated_session_and_durable_command_contracts() -> None:
    base_url = os.environ.get(
        "TRADEFLOW_REAL_STACK_API_URL",
        "http://127.0.0.1:8000",
    )
    access_token = os.environ["TRADEFLOW_REAL_STACK_ACCESS_TOKEN"]
    forbidden_token = os.environ["TRADEFLOW_REAL_STACK_FORBIDDEN_TOKEN"]
    correlation_id = str(uuid4())
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Correlation-ID": correlation_id,
    }

    with httpx.Client(base_url=base_url, timeout=10) as client:
        unauthenticated = client.get("/v1/session")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "authentication_required"

        forbidden = client.get(
            "/v1/session",
            headers={"Authorization": f"Bearer {forbidden_token}"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "capability_required"

        session = client.get("/v1/session", headers=headers)
        assert session.status_code == 200
        assert session.headers["x-correlation-id"] == correlation_id
        assert session.json()["database"] == "ready"

        command_headers = {
            **headers,
            "Idempotency-Key": f"black-box-{uuid4()}",
        }
        first = client.post(
            "/v1/platform/ping",
            headers=command_headers,
            json={"message": "real-stack acceptance"},
        )
        replay = client.post(
            "/v1/platform/ping",
            headers=command_headers,
            json={"message": "real-stack acceptance"},
        )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json() == replay.json()
    UUID(first.json()["command_id"])
    assert first.headers["x-idempotency-replayed"] == "false"
    assert replay.headers["x-idempotency-replayed"] == "true"
