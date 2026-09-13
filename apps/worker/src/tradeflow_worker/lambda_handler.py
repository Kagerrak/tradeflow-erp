"""SQS-triggered worker Lambda.

One SQS message carries one job.  Messages are produced by the S3 notification
on the ``jobs/`` prefix (see :mod:`tradeflow_api.job_queue`), so the queue is a
real SQS queue with visibility timeouts, a redrive policy and a dead-letter
queue, while the VPC-attached producer never needs internet egress.

Failures are reported per message with ``batchItemFailures`` so a single poison
job is retried and eventually dead-lettered without re-running its healthy
siblings.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import unquote_plus
from uuid import UUID

import boto3  # type: ignore[import-untyped]
from tradeflow_api.database import create_database_engine, create_session_factory
from tradeflow_api.job_queue import (
    DEMO_RESET_KIND,
    OUTBOX_EVENT_KIND,
    JobRequest,
    job_from_marker,
)
from tradeflow_api.object_storage import S3ObjectStorage
from tradeflow_api.outbox_jobs import OutboxJobAddress, process_outbox_job

from tradeflow_worker.config import WorkerSettings, get_worker_settings
from tradeflow_worker.demo_reset_job import run_demo_reset

logger = logging.getLogger(__name__)

_settings: WorkerSettings = get_worker_settings()
_engine = create_database_engine(_settings.database_url, lambda_runtime=True)
_session_factory = create_session_factory(_engine)
_object_storage = S3ObjectStorage(_settings)
_s3 = boto3.client("s3", region_name=_settings.resolves_aws_region)


def _job_from_sqs_record(record: dict[str, Any]) -> tuple[JobRequest, str | None, str | None]:
    """Return the job plus the bucket and marker key it came from, if any."""
    body = json.loads(record["body"])
    if not isinstance(body, dict):
        raise ValueError("SQS message body must be a JSON object.")
    s3_records = body.get("Records")
    if isinstance(s3_records, list) and s3_records:
        notification = s3_records[0]
        bucket = str(notification["s3"]["bucket"]["name"])
        key = unquote_plus(str(notification["s3"]["object"]["key"]))
        response = _s3.get_object(Bucket=bucket, Key=key)
        payload = response["Body"].read()
        return job_from_marker(key, payload), bucket, key
    if "kind" in body:
        raw_payload = body.get("payload")
        return (
            JobRequest(
                kind=str(body["kind"]),
                marker_id=str(body.get("marker_id", "")),
                payload=raw_payload if isinstance(raw_payload, dict) else {},
            ),
            None,
            None,
        )
    raise ValueError("Unrecognised SQS job message.")


def _delete_marker(bucket: str | None, key: str | None) -> None:
    if bucket is None or key is None:
        return
    try:
        _s3.delete_object(Bucket=bucket, Key=key)
    except Exception:
        logger.warning("job_marker_delete_failed", exc_info=True)


async def _run_job(job: JobRequest) -> dict[str, Any]:
    if job.kind == OUTBOX_EVENT_KIND:
        address = OutboxJobAddress(
            outbox_event_id=UUID(str(job.payload["outbox_event_id"])),
            handler_group=str(job.payload.get("handler_group", "delivery")),
        )
        result = await process_outbox_job(_session_factory, _object_storage, address)
        return {
            "kind": job.kind,
            "outbox_event_id": result.outbox_event_id,
            "handler_group": result.handler_group,
            "handlers": list(result.completed_handlers),
        }
    if job.kind == DEMO_RESET_KIND:
        return await run_demo_reset(_settings, owner=str(job.payload.get("owner") or "") or None)
    raise ValueError(f"Unsupported job kind {job.kind!r}.")


async def _handle_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for record in records:
        message_id = str(record.get("messageId", "unknown"))
        try:
            job, marker_bucket, marker_key = await asyncio.to_thread(_job_from_sqs_record, record)
            result = await _run_job(job)
            await asyncio.to_thread(_delete_marker, marker_bucket, marker_key)
            logger.info("job_completed", extra={"job": job.kind, "result": result})
        except Exception:
            logger.exception("job_failed", extra={"message_id": message_id})
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """SQS event source entry point."""
    del context
    records = event.get("Records")
    if not isinstance(records, list):
        raise ValueError("Worker Lambda expects an SQS event with a Records list.")
    try:
        return asyncio.run(_handle_records(records))
    finally:
        asyncio.run(_engine.dispose())
