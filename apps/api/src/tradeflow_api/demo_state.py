"""Demo coordination state.

The demo stack needs a small, always-available record of what the evaluation
environment is doing: is it ready, is a refresh running, when is the next
refresh due, and which manifest the web console should show.  That record must
be readable without touching PostgreSQL, because reading it must never wake a
paused Aurora cluster.

Two backends implement the same contract:

* :class:`DynamoDemoStateStore` — the deployed backend.  DynamoDB on-demand is
  reachable from a VPC Lambda through the free DynamoDB gateway endpoint and
  from the web Lambda over the public endpoint, so both tiers can read demo
  status without opening a database connection.
* :class:`FileDemoStateStore` — the local development and test backend.  It
  keeps the historical ``status.json`` / ``manifest.json`` behaviour used by
  ``docker compose`` and the existing test-suite.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

logger = logging.getLogger(__name__)

READY = "ready"
REFRESHING = "refreshing"
FAILED = "failed"

DEFAULT_RESET_INTERVAL_MINUTES = 45
RESET_LOCK_TTL_SECONDS = 15 * 60
STATE_CACHE_SECONDS = 5.0
ACTIVITY_THROTTLE_SECONDS = 60.0

STATE_PARTITION = "demo"
STATE_SORT_KEY = "state"
LOCK_SORT_KEY = "lock"


@dataclass(frozen=True, slots=True)
class DemoState:
    """A snapshot of the demo coordination record."""

    status: str = READY
    seed_version: str | None = None
    next_reset_at: datetime | None = None
    reset_started_at: datetime | None = None
    reset_owner: str | None = None
    last_error: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.status == READY

    def reset_due(self, now: datetime | None = None) -> bool:
        if self.next_reset_at is None:
            return True
        return (now or datetime.now(UTC)) >= self.next_reset_at

    def as_payload(self) -> dict[str, Any]:
        return {
            "lastError": self.last_error,
            "manifest": self.manifest,
            "nextResetAt": _iso(self.next_reset_at),
            "resetStartedAt": _iso(self.reset_started_at),
            "seedVersion": self.seed_version,
            "status": self.status,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_epoch(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC)


def _to_epoch(value: datetime | None) -> int | None:
    return int(value.timestamp()) if value is not None else None


class DemoStateStore(Protocol):
    """Coordination contract shared by the DynamoDB and file backends."""

    async def read(self) -> DemoState: ...

    async def mark_refreshing(self, *, owner: str, seed_version: str) -> None: ...

    async def mark_ready(
        self,
        *,
        manifest: dict[str, Any],
        seed_version: str,
        reset_interval_minutes: int,
    ) -> None: ...

    async def mark_failed(self, *, reason: str) -> None: ...

    async def record_activity(self) -> None: ...

    async def try_claim_reset(self, *, owner: str, ttl_seconds: int) -> bool: ...

    async def release_reset_lock(self, *, owner: str) -> None: ...


class CachedDemoState:
    """Read-through cache that keeps coordination reads to one per window.

    The cache is per Lambda execution environment and deliberately short: a
    refresh that completes in one environment must become visible in another
    within a few seconds.
    """

    def __init__(self, store: DemoStateStore, ttl_seconds: float = STATE_CACHE_SECONDS) -> None:
        self._store = store
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._expires_at = 0.0
        self._value: DemoState | None = None
        self._last_activity = 0.0

    async def read(self, *, fresh: bool = False) -> DemoState:
        if not fresh and self._value is not None and time.monotonic() < self._expires_at:
            return self._value
        async with self._lock:
            if not fresh and self._value is not None and time.monotonic() < self._expires_at:
                return self._value
            self._value = await self._store.read()
            self._expires_at = time.monotonic() + self._ttl
            return self._value

    def invalidate(self) -> None:
        self._value = None
        self._expires_at = 0.0

    async def record_activity(self, *, throttle_seconds: float = ACTIVITY_THROTTLE_SECONDS) -> None:
        now = time.monotonic()
        if now - self._last_activity < throttle_seconds:
            return
        self._last_activity = now
        await self._store.record_activity()


class DynamoDemoStateStore:
    """Demo coordination backed by a DynamoDB on-demand table."""

    def __init__(self, table_name: str, *, region: str | None = None) -> None:
        import boto3  # type: ignore[import-untyped]

        self._table_name = table_name
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def _state_key(self) -> dict[str, str]:
        return {"pk": STATE_PARTITION, "sk": STATE_SORT_KEY}

    def _lock_key(self) -> dict[str, str]:
        return {"pk": STATE_PARTITION, "sk": LOCK_SORT_KEY}

    async def read(self) -> DemoState:
        response = await asyncio.to_thread(
            self._table.get_item, Key=self._state_key(), ConsistentRead=True
        )
        item = response.get("Item")
        if not item:
            return DemoState()
        manifest = item.get("manifest")
        return DemoState(
            status=str(item.get("status", READY)),
            seed_version=cast("str | None", item.get("seed_version")),
            next_reset_at=_from_epoch(item.get("next_reset_at")),
            reset_started_at=_from_epoch(item.get("reset_started_at")),
            reset_owner=cast("str | None", item.get("reset_owner")),
            last_error=cast("str | None", item.get("last_error")),
            manifest=cast("dict[str, Any]", manifest) if isinstance(manifest, dict) else {},
        )

    async def mark_refreshing(self, *, owner: str, seed_version: str) -> None:
        now = datetime.now(UTC)
        await asyncio.to_thread(
            self._table.update_item,
            Key=self._state_key(),
            UpdateExpression=(
                "SET #status = :status, seed_version = :seed, reset_owner = :owner, "
                "reset_started_at = :started, last_error = :none"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": REFRESHING,
                ":seed": seed_version,
                ":owner": owner,
                ":started": _to_epoch(now),
                ":none": None,
            },
        )

    async def mark_ready(
        self,
        *,
        manifest: dict[str, Any],
        seed_version: str,
        reset_interval_minutes: int,
    ) -> None:
        now = datetime.now(UTC)
        next_reset = now + timedelta(minutes=reset_interval_minutes)
        await asyncio.to_thread(
            self._table.update_item,
            Key=self._state_key(),
            UpdateExpression=(
                "SET #status = :status, seed_version = :seed, next_reset_at = :next, "
                "manifest = :manifest, last_error = :none, refreshed_at = :now "
                "REMOVE reset_owner, reset_started_at"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": READY,
                ":seed": seed_version,
                ":next": _to_epoch(next_reset),
                ":manifest": manifest,
                ":none": None,
                ":now": _to_epoch(now),
            },
        )

    async def mark_failed(self, *, reason: str) -> None:
        await asyncio.to_thread(
            self._table.update_item,
            Key=self._state_key(),
            UpdateExpression=(
                "SET #status = :status, last_error = :error REMOVE reset_owner, reset_started_at"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": FAILED, ":error": reason[:1000]},
        )

    async def record_activity(self) -> None:
        await asyncio.to_thread(
            self._table.update_item,
            Key=self._state_key(),
            UpdateExpression="SET last_activity_at = :now",
            ExpressionAttributeValues={":now": _to_epoch(datetime.now(UTC))},
        )

    async def try_claim_reset(self, *, owner: str, ttl_seconds: int) -> bool:
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        try:
            await asyncio.to_thread(
                self._table.put_item,
                Item={
                    **self._lock_key(),
                    "owner": owner,
                    "acquired_at": _to_epoch(now),
                    "expires_at": _to_epoch(expires_at),
                    "ttl": _to_epoch(expires_at),
                },
                ConditionExpression="attribute_not_exists(sk) OR expires_at < :now",
                ExpressionAttributeValues={":now": _to_epoch(now)},
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    async def release_reset_lock(self, *, owner: str) -> None:
        await asyncio.to_thread(
            self._table.delete_item,
            Key=self._lock_key(),
            ConditionExpression="attribute_not_exists(sk) OR #owner = :owner",
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":owner": owner},
        )


class FileDemoStateStore:
    """File-backed coordination for local development and tests."""

    def __init__(self, state_path: Path, *, reset_interval_minutes: int) -> None:
        self._state_path = state_path
        self._manifest_path = state_path.parent / "manifest.json"
        self._reset_interval_minutes = reset_interval_minutes

    async def read(self) -> DemoState:
        return await asyncio.to_thread(self._read_sync)

    def _read_sync(self) -> DemoState:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DemoState(status=REFRESHING)
        manifest: dict[str, Any] = {}
        try:
            loaded = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, ValueError):
            manifest = {}
        next_reset = payload.get("next_reset_at")
        return DemoState(
            status=str(payload.get("status", REFRESHING)),
            seed_version=cast("str | None", payload.get("seed_version")),
            next_reset_at=(
                datetime.fromisoformat(str(next_reset)) if isinstance(next_reset, str) else None
            ),
            manifest=manifest,
        )

    async def mark_refreshing(self, *, owner: str, seed_version: str) -> None:
        del owner
        await asyncio.to_thread(self._write_sync, REFRESHING, seed_version, None)

    async def mark_ready(
        self,
        *,
        manifest: dict[str, Any],
        seed_version: str,
        reset_interval_minutes: int,
    ) -> None:
        await asyncio.to_thread(
            self._write_sync,
            READY,
            seed_version,
            datetime.now(UTC) + timedelta(minutes=reset_interval_minutes),
        )
        await asyncio.to_thread(self._write_manifest_sync, manifest)

    async def mark_failed(self, *, reason: str) -> None:
        await asyncio.to_thread(self._write_sync, FAILED, None, None, reason)

    async def record_activity(self) -> None:
        return None

    async def try_claim_reset(self, *, owner: str, ttl_seconds: int) -> bool:
        del owner, ttl_seconds
        return True

    async def release_reset_lock(self, *, owner: str) -> None:
        del owner
        return None

    def _write_sync(
        self,
        status: str,
        seed_version: str | None,
        next_reset: datetime | None,
        last_error: str | None = None,
    ) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_error": last_error,
            "next_reset_at": _iso(next_reset),
            "seed_version": seed_version,
            "status": status,
        }
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        temporary.replace(self._state_path)

    def _write_manifest_sync(self, manifest: dict[str, Any]) -> None:
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(self._manifest_path)

    @property
    def reset_interval_minutes(self) -> int:
        return self._reset_interval_minutes
