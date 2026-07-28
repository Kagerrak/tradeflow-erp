from __future__ import annotations

from typing import Any

import structlog
from arq.connections import RedisSettings

from tradeflow_worker.config import get_worker_settings

logger = structlog.get_logger()


async def startup(_: dict[str, Any]) -> None:
    await logger.ainfo("worker_started", service="tradeflow-worker")


async def shutdown(_: dict[str, Any]) -> None:
    await logger.ainfo("worker_stopped", service="tradeflow-worker")


async def heartbeat(_: dict[str, Any]) -> dict[str, str]:
    return {"service": "tradeflow-worker", "status": "ok"}


class Worker:
    settings = get_worker_settings()
    functions = [heartbeat]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 300
