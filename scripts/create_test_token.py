from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta

import jwt


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise SystemExit(f"{name} is required.")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a short-lived local TradeFlow test token.")
    parser.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        help="Capability to include; repeat for multiple capabilities.",
    )
    args = parser.parse_args()
    environment = os.environ.get("TRADEFLOW_ENVIRONMENT", "development")
    if environment not in {"development", "testing"}:
        raise SystemExit("Test tokens are only available in development and testing.")

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "aud": os.environ.get("TRADEFLOW_AUTH_AUDIENCE", "tradeflow-api"),
            "capabilities": args.capabilities
            if args.capabilities is not None
            else ["platform:read", "platform:write"],
            "exp": now + timedelta(minutes=15),
            "iat": now,
            "iss": required_environment("TRADEFLOW_AUTH_ISSUER"),
            "name": "Local Platform Operator",
            "sub": "local-platform-operator",
        },
        required_environment("TRADEFLOW_AUTH_TEST_SECRET"),
        algorithm="HS256",
    )
    print(token)


if __name__ == "__main__":
    main()
