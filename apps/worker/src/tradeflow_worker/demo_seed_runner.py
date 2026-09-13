"""Re-seed the demo dataset from inside a Lambda.

The demo seeder exercises the real business API (organization bootstrap, sales
orders, picking, dispatch, delivery confirmation, invoicing, payments,
transfers, adjustments and returns) rather than writing tables directly, so a
refresh proves the whole stack works.  To keep that property without needing
network egress from the VPC, the API is served in-process on loopback and the
seeder — unchanged — talks to it over HTTP.
"""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

logger = logging.getLogger(__name__)


def _seeder_entry() -> str:
    return os.environ.get("TRADEFLOW_DEMO_SEED_MODULE", "scripts.seed_demo")


def _run_seeder_sync() -> None:
    """Run the synchronous seeder on a worker thread.

    The thread has no running event loop, which the seeder needs because it
    calls ``asyncio.run`` for a couple of direct PostgreSQL writer helpers.
    """
    module_name, _, attribute = _seeder_entry().partition(":")
    module = __import__(module_name, fromlist=["Seeder"])
    del attribute
    seeder = module.Seeder()
    try:
        seeder.run()
    finally:
        seeder.client.close()


async def run_seed_against_in_process_api(*, port: int) -> None:
    from tradeflow_api.app import create_app

    app = create_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(1200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("The in-process API did not start in time.")
        await asyncio.to_thread(_run_seeder_sync)
    finally:
        server.should_exit = True
        await task
