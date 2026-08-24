from __future__ import annotations

import json
from pathlib import Path

import pytest
from tradeflow_api.demo_reset import (
    DEMO_SEED_VERSION,
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
