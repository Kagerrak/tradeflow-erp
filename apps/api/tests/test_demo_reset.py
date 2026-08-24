from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tradeflow_api.demo_reset import (
    DEMO_SEED_VERSION,
    DemoMaintenanceMiddleware,
    maintenance_state,
    require_safe_demo_database,
)


def test_reset_refuses_non_demo_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEFLOW_DEMO_MODE", "enabled")
    with pytest.raises(RuntimeError, match="Refusing to reset"):
        require_safe_demo_database(
            "postgresql+asyncpg://tradeflow:secret@db/tradeflow_demo",
            "production",
            "tradeflow_demo",
        )


def test_reset_refuses_database_name_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEFLOW_DEMO_MODE", "enabled")
    with pytest.raises(RuntimeError, match="Refusing to reset"):
        require_safe_demo_database(
            "postgresql+asyncpg://tradeflow:secret@db/tradeflow", "demo", "tradeflow_demo"
        )


def test_maintenance_state_records_success(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    with maintenance_state(path):
        assert json.loads(path.read_text())["status"] == "refreshing"
    ready = json.loads(path.read_text())
    assert ready["seed_version"] == DEMO_SEED_VERSION
    assert ready["status"] == "ready"
    assert ready["next_reset_at"] is not None


def test_maintenance_state_records_failure(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    with pytest.raises(RuntimeError), maintenance_state(path):
        raise RuntimeError("seed failed")
    assert json.loads(path.read_text())["status"] == "failed"


@pytest.mark.asyncio
async def test_refreshing_state_rejects_operational_api_requests(tmp_path: Path) -> None:
    state_path = tmp_path / "status.json"
    state_path.write_text('{"status":"refreshing"}\n', encoding="utf-8")
    app = FastAPI()
    reset_token = "reset-token-with-at-least-thirty-two-characters"  # noqa: S105
    app.add_middleware(
        DemoMaintenanceMiddleware,
        state_path=state_path,
        reset_token=reset_token,
    )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/orders")
    async def orders() -> dict[str, list[object]]:
        return {"items": []}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live_response = await client.get("/health/live")
        blocked = await client.get("/v1/orders")
        incorrect = await client.get("/v1/orders", headers={"X-TradeFlow-Demo-Reset": "incorrect"})
        trusted = await client.get("/v1/orders", headers={"X-TradeFlow-Demo-Reset": reset_token})

    assert live_response.status_code == 200
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "demo_refreshing"
    assert blocked.headers["retry-after"] == "30"
    assert incorrect.status_code == 503
    assert trusted.status_code == 200
