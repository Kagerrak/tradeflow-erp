"""Contract tests for the serverless worker entry point."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tradeflow_api.job_queue import (
    DEMO_RESET_KIND,
    OUTBOX_EVENT_KIND,
    JobRequest,
    demo_reset_job,
    job_from_marker,
    outbox_event_job,
)
from tradeflow_worker.config import WorkerSettings


def test_worker_settings_defaults_to_local_database() -> None:
    settings = WorkerSettings(environment="testing")

    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.outbox_batch_size >= 1


def test_handler_groups_get_distinct_markers() -> None:
    event_id = "11111111-1111-4111-8111-111111111111"

    delivery = outbox_event_job(event_id, "delivery")
    notifications = outbox_event_job(event_id, "notifications")

    assert delivery.object_key != notifications.object_key


def test_job_marker_round_trips() -> None:
    job = outbox_event_job("11111111-1111-4111-8111-111111111111", "delivery")
    body = json.dumps(job.as_message()).encode("utf-8")

    restored = job_from_marker(job.object_key, body)

    assert restored == job


def test_demo_reset_job_marker_is_deterministic() -> None:
    first = demo_reset_job("2026-09-13T00:00:00+00:00")
    second = demo_reset_job("2026-09-13T00:00:00+00:00")

    assert first.object_key == second.object_key
    assert first.kind == DEMO_RESET_KIND


def test_outbox_job_kind_is_stable() -> None:
    assert outbox_event_job("abc", "delivery").kind == OUTBOX_EVENT_KIND


@pytest.mark.parametrize(
    ("kind", "marker_id"),
    [
        (OUTBOX_EVENT_KIND, "outbox-event-id"),
        (DEMO_RESET_KIND, "2026-09-13T00:00:00+00:00"),
    ],
)
def test_job_object_keys_are_namespaced_by_kind(kind: str, marker_id: str) -> None:
    job = JobRequest(kind=kind, marker_id=marker_id, payload={})

    assert job.object_key == f"jobs/{kind}/{marker_id}.json"


def test_job_from_marker_rejects_non_object_payload() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        job_from_marker("jobs/x.json", b"[]")


def test_sqs_record_without_job_shape_is_rejected() -> None:
    from tradeflow_worker import lambda_handler

    record: dict[str, Any] = {"messageId": "1", "body": json.dumps({"unexpected": True})}

    with pytest.raises(ValueError, match="Unrecognised"):
        lambda_handler._job_from_sqs_record(record)  # noqa: SLF001
