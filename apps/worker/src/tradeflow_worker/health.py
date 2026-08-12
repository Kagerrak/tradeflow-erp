from __future__ import annotations

from redis.asyncio import Redis

from tradeflow_worker.config import WorkerSettings


async def check_redis(settings: WorkerSettings) -> dict[str, str]:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        ready = await client.ping()
        if not ready:
            raise RuntimeError("Redis did not acknowledge the health check.")
        return {"service": "tradeflow-worker", "status": "ready"}
    finally:
        await client.aclose()
