from __future__ import annotations

import pytest
from tradeflow_worker.config import WorkerSettings
from tradeflow_worker.health import check_redis


@pytest.mark.asyncio
async def test_worker_reports_real_redis_ready() -> None:
    settings = WorkerSettings(
        environment="testing",
        redis_url="redis://localhost:6380/0",
        telemetry_enabled=False,
    )

    assert await check_redis(settings) == {
        "service": "tradeflow-worker",
        "status": "ready",
    }
