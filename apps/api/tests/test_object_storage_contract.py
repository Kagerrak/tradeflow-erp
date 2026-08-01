from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from tradeflow_api.object_storage import S3ObjectStorage


@dataclass(frozen=True)
class StorageSettings:
    object_storage_access_key: str = "tradeflow"
    object_storage_bucket: str = "tradeflow-evidence"
    object_storage_endpoint_url: str = "http://127.0.0.1:9000"
    object_storage_public_endpoint_url: str = "http://127.0.0.1:9000"
    object_storage_secret_key: str = "tradeflow-local-only"  # noqa: S105
    object_storage_url_expiry_seconds: int = 1


@pytest.mark.asyncio
async def test_minio_resumes_parts_and_enforces_signed_integrity_access() -> None:
    storage = S3ObjectStorage(StorageSettings())
    await storage.ensure_bucket()
    first = b"a" * (5 * 1024 * 1024)
    second = b"final-part"
    content = first + second
    digest = sha256(content).hexdigest()
    object_key = f"contract/multipart/{uuid4()}"
    upload_id = await storage.create_multipart_upload(
        content_type="image/png",
        object_key=object_key,
        sha256=digest,
    )
    async with httpx.AsyncClient() as client:
        first_upload_url = storage.signed_upload_part_url(
            object_key=object_key,
            part_number=1,
            upload_id=upload_id,
        )
        tampered_upload = await client.put(
            first_upload_url.replace("partNumber=1", "partNumber=2"),
            content=b"tampered",
        )
        assert tampered_upload.status_code == 403
        first_response = await client.put(
            first_upload_url,
            content=first,
        )
        assert first_response.status_code == 200, first_response.text
        resumed = await storage.list_uploaded_parts(
            object_key=object_key,
            upload_id=upload_id,
        )
        assert [(part.number, part.size_bytes) for part in resumed] == [(1, len(first))]
        second_response = await client.put(
            storage.signed_upload_part_url(
                object_key=object_key,
                part_number=2,
                upload_id=upload_id,
            ),
            content=second,
        )
        assert second_response.status_code == 200, second_response.text
        parts = await storage.list_uploaded_parts(
            object_key=object_key,
            upload_id=upload_id,
        )
        await storage.complete_multipart_upload(
            object_key=object_key,
            parts=parts,
            upload_id=upload_id,
        )
        metadata = await storage.head(object_key)
        assert metadata.content_type == "image/png"
        assert metadata.sha256 == digest
        assert metadata.size_bytes == len(content)
        assert await storage.computed_sha256(object_key) == digest

        access_url = storage.signed_get_url(object_key=object_key)
        access = await client.get(access_url)
        assert access.status_code == 200
        assert access.content == content
        tampered = await client.get(f"{access_url}&response-content-type=text/plain")
        assert tampered.status_code == 403
        expiring_key = f"contract/multipart-expiry/{uuid4()}"
        expiring_upload_id = await storage.create_multipart_upload(
            content_type="image/png",
            object_key=expiring_key,
            sha256=sha256(b"late").hexdigest(),
        )
        expired_upload_url = storage.signed_upload_part_url(
            object_key=expiring_key,
            part_number=1,
            upload_id=expiring_upload_id,
        )
        await asyncio.sleep(2)
        expired = await client.get(access_url)
        assert expired.status_code == 403
        expired_upload = await client.put(expired_upload_url, content=b"late")
        assert expired_upload.status_code == 403
