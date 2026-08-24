from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

DEMO_LOCK_ID = 8_604_501_202_608_241
DEMO_SEED_VERSION = "2026.08.24.1"


def require_safe_demo_database(database_url: str, environment: str, expected_name: str) -> None:
    database_name = make_url(database_url).database
    if (
        environment != "demo"
        or os.environ.get("TRADEFLOW_DEMO_MODE") not in {"1", "true", "enabled"}
        or database_name != expected_name
        or not expected_name.startswith("tradeflow_demo")
    ):
        raise RuntimeError(
            "Refusing to reset unless the demo environment, demo mode, and an explicit "
            "tradeflow_demo database name all agree."
        )


@contextmanager
def maintenance_state(state_path: Path) -> Iterator[None]:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_state(state_path, "refreshing")
    try:
        yield
    except Exception:
        _write_state(state_path, "failed")
        raise
    else:
        _write_state(state_path, "ready")


def _write_state(state_path: Path, status: str) -> None:
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "next_reset_at": (datetime.now(UTC) + timedelta(minutes=45)).isoformat()
                if status == "ready"
                else None,
                "seed_version": DEMO_SEED_VERSION,
                "status": status,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(state_path)
