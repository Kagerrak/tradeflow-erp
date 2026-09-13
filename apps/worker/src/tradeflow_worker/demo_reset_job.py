"""Rebuild the demo dataset on renewed activity.

The refresh is triggered by expiry, not by a timer: the API tier notices that
the seeded data is older than the refresh interval and hands a job to this
worker through the queue.  Nothing runs, and Aurora stays paused, while the
demo is idle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from tradeflow_api.database import create_database_engine
from tradeflow_api.demo_reset import (
    DEMO_LOCK_ID,
    missing_demo_seed_requirements,
    require_safe_demo_database,
)
from tradeflow_api.demo_state import (
    DynamoDemoStateStore,
    FileDemoStateStore,
)
from tradeflow_api.models import metadata

from tradeflow_worker.config import WorkerSettings
from tradeflow_worker.demo_seed_runner import run_seed_against_in_process_api

logger = logging.getLogger(__name__)

RESET_LOCK_TTL_SECONDS = 15 * 60


def _engine(settings: WorkerSettings) -> Any:
    return create_database_engine(
        settings.database_url,
        iam_auth=settings.db_iam_auth,
        region=settings.resolves_aws_region,
    )


def build_state_store(settings: WorkerSettings) -> Any:
    if settings.demo_state_backend == "dynamodb":
        if settings.demo_state_table is None:
            raise RuntimeError("TRADEFLOW_WORKER_DEMO_STATE_TABLE is required.")
        return DynamoDemoStateStore(settings.demo_state_table, region=settings.resolves_aws_region)
    state_path = settings.demo_state_path or str(Path(settings.demo_state_dir) / "status.json")
    return FileDemoStateStore(
        Path(state_path),
        reset_interval_minutes=settings.demo_reset_interval_minutes,
    )


def _write_credential(settings: WorkerSettings, token: str) -> None:
    if not settings.demo_web_credential_parameter:
        return
    import boto3  # type: ignore[import-untyped]

    client = boto3.client("ssm", region_name=settings.resolves_aws_region)
    client.put_parameter(
        Name=settings.demo_web_credential_parameter,
        Value=token,
        Type="SecureString",
        Overwrite=True,
    )


async def _truncate(settings: WorkerSettings) -> None:
    engine = _engine(settings)
    try:
        async with engine.begin() as connection:
            table_names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
            await connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))
    finally:
        await engine.dispose()


async def _validate(settings: WorkerSettings) -> list[str]:
    engine = _engine(settings)
    try:
        async with engine.connect() as connection:
            return await missing_demo_seed_requirements(connection)
    finally:
        await engine.dispose()


async def run_demo_reset(settings: WorkerSettings, *, owner: str | None = None) -> dict[str, Any]:
    """Rebuild demo data.  Safe to invoke concurrently: the losers no-op."""
    require_safe_demo_database(
        settings.database_url,
        settings.environment,
        settings.demo_database_name,
    )
    store = build_state_store(settings)
    lock_owner = owner or f"worker-{uuid4()}"

    if not await store.try_claim_reset(owner=lock_owner, ttl_seconds=RESET_LOCK_TTL_SECONDS):
        logger.info("demo_reset_skipped_lock_held", extra={"owner": lock_owner})
        return {"status": "skipped", "reason": "reset lock held"}

    state_dir = Path(settings.demo_state_dir)
    seeded = False
    try:
        await store.mark_refreshing(owner=lock_owner, seed_version=settings.demo_seed_version)
        engine = _engine(settings)
        try:
            async with engine.connect() as lock_connection:
                acquired = await lock_connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": DEMO_LOCK_ID}
                )
                if not acquired:
                    return {"status": "skipped", "reason": "postgres advisory lock held"}
                try:
                    await _truncate(settings)
                    os.environ.setdefault("TRADEFLOW_DEMO_STATE_DIR", str(state_dir))
                    os.environ.setdefault("TRADEFLOW_DEMO_WEB_UID", str(os.getuid()))
                    if not os.environ.get("TRADEFLOW_DEMO_STATE_PATH"):
                        os.environ["TRADEFLOW_DEMO_STATE_PATH"] = str(state_dir / "status.json")
                    os.environ.setdefault(
                        "TRADEFLOW_DEMO_API_URL",
                        f"http://127.0.0.1:{settings.demo_reset_api_port}",
                    )
                    await run_seed_against_in_process_api(port=settings.demo_reset_api_port)

                    missing = await _validate(settings)
                    if missing:
                        raise RuntimeError("Demo seed is incomplete: " + ", ".join(sorted(missing)))

                    credential = (state_dir / "credential").read_text(encoding="utf-8").strip()
                    manifest_path = state_dir / "manifest.json"
                    manifest = (
                        json.loads(manifest_path.read_text(encoding="utf-8"))
                        if manifest_path.exists()
                        else {}
                    )
                    if not isinstance(manifest, dict):
                        manifest = {}
                    await asyncio.to_thread(_write_credential, settings, credential)
                    await store.mark_ready(
                        manifest=manifest,
                        seed_version=settings.demo_seed_version,
                        reset_interval_minutes=settings.demo_reset_interval_minutes,
                    )
                    seeded = True
                finally:
                    await lock_connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": DEMO_LOCK_ID}
                    )
        finally:
            await engine.dispose()
        logger.info("demo_reset_completed", extra={"owner": lock_owner})
        return {"status": "ready", "seed_version": settings.demo_seed_version}
    except Exception as error:
        logger.exception("demo_reset_failed")
        await store.mark_failed(reason=str(error))
        raise
    finally:
        await store.release_reset_lock(owner=lock_owner)
        if not seeded:
            logger.info("demo_reset_not_ready", extra={"owner": lock_owner})
