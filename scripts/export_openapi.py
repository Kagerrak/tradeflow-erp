from __future__ import annotations

import json
from pathlib import Path

from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


def main() -> None:
    settings = Settings(
        environment="testing",
        database_url=("postgresql+asyncpg://tradeflow:tradeflow@localhost:5433/tradeflow_test"),
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="openapi-export-secret-with-32-characters",
        telemetry_enabled=False,
    )
    schema = create_app(settings).openapi()
    destination = Path("openapi/openapi.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
