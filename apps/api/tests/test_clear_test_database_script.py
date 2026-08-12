from __future__ import annotations

import pytest
from tradeflow_api.test_database import require_safe_test_database


def test_clear_test_database_requires_testing_environment_and_test_name() -> None:
    require_safe_test_database(
        "postgresql+asyncpg://tradeflow:tradeflow@localhost:5433/tradeflow_test",
        "testing",
    )

    with pytest.raises(RuntimeError, match="Refusing to clear"):
        require_safe_test_database(
            "postgresql+asyncpg://tradeflow:tradeflow@localhost:5432/tradeflow",
            "testing",
        )
    with pytest.raises(RuntimeError, match="Refusing to clear"):
        require_safe_test_database(
            "postgresql+asyncpg://tradeflow:tradeflow@localhost:5433/tradeflow_test",
            "production",
        )
