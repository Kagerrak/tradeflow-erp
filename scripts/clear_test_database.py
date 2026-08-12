from __future__ import annotations

import asyncio

from tradeflow_api.test_database import clear_test_database

if __name__ == "__main__":
    asyncio.run(clear_test_database())
