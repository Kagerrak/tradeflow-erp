"""Durable job hand-off between the API tier and the background worker.

The API Lambda runs inside the VPC so that it can reach private Aurora.  A
VPC-attached Lambda has no internet egress unless a NAT gateway or a paid
interface endpoint is added, and this deployment deliberately has neither.
The job queue therefore uses a transport the VPC already reaches for free: the
S3 gateway endpoint.

A job is written as a small JSON marker object under ``jobs/``.  The bucket
notification on that prefix publishes the marker to the SQS work queue, and the
worker Lambda consumes it.  The marker key is derived from the job identity, so
re-publishing a job is an idempotent overwrite that always re-fires the
notification — which is exactly what recovery needs.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol

OUTBOX_EVENT_KIND = "outbox_event"
DEMO_RESET_KIND = "demo_reset"


@dataclass(frozen=True, slots=True)
class JobRequest:
    """A unit of background work, addressed by a stable marker key."""

    kind: str
    marker_id: str
    payload: dict[str, Any]

    @property
    def object_key(self) -> str:
        return f"jobs/{self.kind}/{self.marker_id}.json"

    def as_message(self) -> dict[str, Any]:
        return {"kind": self.kind, "marker_id": self.marker_id, "payload": self.payload}


def outbox_event_job(outbox_event_id: str, handler_group: str) -> JobRequest:
    """One job per (event, handler group): groups retry independently."""
    return JobRequest(
        kind=OUTBOX_EVENT_KIND,
        marker_id=f"{outbox_event_id}:{handler_group}",
        payload={"outbox_event_id": outbox_event_id, "handler_group": handler_group},
    )


def demo_reset_job(owner: str) -> JobRequest:
    return JobRequest(kind=DEMO_RESET_KIND, marker_id=owner, payload={"owner": owner})


class JobPublisher(Protocol):
    async def publish(self, job: JobRequest) -> None: ...


class S3JobPublisher:
    """Writes job markers to S3; the bucket notification fans out to SQS."""

    def __init__(self, bucket: str, *, region: str | None = None) -> None:
        import boto3  # type: ignore[import-untyped]

        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    @property
    def bucket(self) -> str:
        return self._bucket

    async def publish(self, job: JobRequest) -> None:
        body = json.dumps(job.as_message(), separators=(",", ":")).encode("utf-8")
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=job.object_key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )


class RecordingJobPublisher:
    """In-memory publisher used by tests and by local development."""

    def __init__(self) -> None:
        self.jobs: list[JobRequest] = []

    async def publish(self, job: JobRequest) -> None:
        self.jobs.append(job)


def job_from_marker(key: str, body: bytes) -> JobRequest:
    """Rebuild a job from its S3 marker object."""
    del key
    decoded = json.loads(body.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Job marker must be a JSON object.")
    kind = str(decoded["kind"])
    marker_id = str(decoded["marker_id"])
    payload = decoded.get("payload")
    return JobRequest(
        kind=kind,
        marker_id=marker_id,
        payload=payload if isinstance(payload, dict) else {},
    )
