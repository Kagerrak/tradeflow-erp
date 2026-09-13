"""Local development worker.

The deployed worker is SQS-triggered and has no polling loop.  Local
development has no SQS, so this runner drains the same outbox with the same
bounded query on a short interval.  Only the trigger differs — the processing
code path is identical, which is what keeps local behaviour honest.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from tradeflow_api.database import create_database_engine, create_session_factory
from tradeflow_api.object_storage import S3ObjectStorage
from tradeflow_api.outbox_jobs import drain_pending_outbox

from tradeflow_worker.config import get_worker_settings

logger = logging.getLogger(__name__)


async def run_once() -> int:
    settings = get_worker_settings()
    engine = create_database_engine(
        settings.database_url,
        iam_auth=settings.db_iam_auth,
        region=settings.resolves_aws_region,
    )
    session_factory = create_session_factory(engine)
    object_storage = S3ObjectStorage(settings)
    processed = 0
    try:
        result = await drain_pending_outbox(
            session_factory,
            object_storage,
            limit=settings.outbox_batch_size,
            groups=("delivery", "notifications"),
        )
        processed = result["completed"]
    finally:
        await engine.dispose()
    return processed


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_worker_settings()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(getattr(signal, signal_name), stopping.set)
    logger.info("local_worker_started")
    while not stopping.is_set():
        try:
            processed = await run_once()
            if processed:
                logger.info("local_worker_processed", extra={"count": processed})
        except Exception:
            logger.exception("local_worker_iteration_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=settings.local_poll_seconds)
    logger.info("local_worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
