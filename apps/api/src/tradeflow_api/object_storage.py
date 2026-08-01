from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.client import BaseClient  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from fastapi import Request


@dataclass(frozen=True, slots=True)
class StoredObjectMetadata:
    checksum_sha256: str
    content_type: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class UploadedPart:
    etag: str
    number: int
    size_bytes: int


class ObjectStorage(Protocol):
    @property
    def url_expiry_seconds(self) -> int: ...

    async def ensure_bucket(self) -> None: ...

    async def create_multipart_upload(
        self,
        *,
        content_type: str,
        object_key: str,
        sha256: str,
    ) -> str: ...

    def signed_upload_part_url(
        self, *, object_key: str, part_number: int, upload_id: str
    ) -> str: ...

    async def list_uploaded_parts(
        self, *, object_key: str, upload_id: str
    ) -> list[UploadedPart]: ...

    async def complete_multipart_upload(
        self, *, object_key: str, parts: list[UploadedPart], upload_id: str
    ) -> None: ...

    async def head(self, object_key: str) -> StoredObjectMetadata: ...

    async def computed_sha256(self, object_key: str) -> str: ...

    async def put(self, *, body: bytes, content_type: str, object_key: str) -> None: ...

    def signed_get_url(self, *, object_key: str) -> str: ...


class ObjectStorageConfiguration(Protocol):
    object_storage_access_key: str
    object_storage_bucket: str
    object_storage_endpoint_url: str
    object_storage_public_endpoint_url: str
    object_storage_secret_key: str
    object_storage_url_expiry_seconds: int


class S3ObjectStorage:
    def __init__(self, settings: ObjectStorageConfiguration) -> None:
        self._bucket = settings.object_storage_bucket
        self._expiry = settings.object_storage_url_expiry_seconds
        self._client: BaseClient = boto3.client(
            "s3",
            endpoint_url=settings.object_storage_endpoint_url,
            aws_access_key_id=settings.object_storage_access_key,
            aws_secret_access_key=settings.object_storage_secret_key,
            region_name="us-east-1",
        )
        self._public_client: BaseClient = boto3.client(
            "s3",
            endpoint_url=settings.object_storage_public_endpoint_url,
            aws_access_key_id=settings.object_storage_access_key,
            aws_secret_access_key=settings.object_storage_secret_key,
            region_name="us-east-1",
        )

    @property
    def url_expiry_seconds(self) -> int:
        return self._expiry

    async def ensure_bucket(self) -> None:
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        except ClientError as error:
            status = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status not in {403, 404}:
                raise
            await asyncio.to_thread(self._client.create_bucket, Bucket=self._bucket)

    async def create_multipart_upload(
        self,
        *,
        content_type: str,
        object_key: str,
        sha256: str,
    ) -> str:
        response = await asyncio.to_thread(
            self._client.create_multipart_upload,
            Bucket=self._bucket,
            ContentType=content_type,
            Key=object_key,
            Metadata={"sha256": sha256},
        )
        return str(response["UploadId"])

    def signed_upload_part_url(self, *, object_key: str, part_number: int, upload_id: str) -> str:
        return str(
            self._public_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket,
                    "Key": object_key,
                    "PartNumber": part_number,
                    "UploadId": upload_id,
                },
                ExpiresIn=self._expiry,
                HttpMethod="PUT",
            )
        )

    async def list_uploaded_parts(self, *, object_key: str, upload_id: str) -> list[UploadedPart]:
        response = await asyncio.to_thread(
            self._client.list_parts,
            Bucket=self._bucket,
            Key=object_key,
            UploadId=upload_id,
        )
        return [
            UploadedPart(
                etag=str(part["ETag"]),
                number=int(part["PartNumber"]),
                size_bytes=int(part["Size"]),
            )
            for part in response.get("Parts", [])
        ]

    async def complete_multipart_upload(
        self, *, object_key: str, parts: list[UploadedPart], upload_id: str
    ) -> None:
        await asyncio.to_thread(
            self._client.complete_multipart_upload,
            Bucket=self._bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"ETag": part.etag, "PartNumber": part.number}
                    for part in sorted(parts, key=lambda value: value.number)
                ]
            },
        )

    async def head(self, object_key: str) -> StoredObjectMetadata:
        response = await asyncio.to_thread(
            self._client.head_object,
            Bucket=self._bucket,
            ChecksumMode="ENABLED",
            Key=object_key,
        )
        metadata = response.get("Metadata", {})
        return StoredObjectMetadata(
            checksum_sha256=str(response.get("ChecksumSHA256", "")),
            content_type=str(response.get("ContentType", "")),
            sha256=str(metadata.get("sha256", "")),
            size_bytes=int(response.get("ContentLength", 0)),
        )

    async def computed_sha256(self, object_key: str) -> str:
        response = await asyncio.to_thread(
            self._client.get_object,
            Bucket=self._bucket,
            Key=object_key,
        )
        body = response["Body"]
        content = await asyncio.to_thread(body.read)
        await asyncio.to_thread(body.close)
        return sha256(content).hexdigest()

    async def put(self, *, body: bytes, content_type: str, object_key: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Body=body,
            Bucket=self._bucket,
            ContentType=content_type,
            Key=object_key,
        )

    def signed_get_url(self, *, object_key: str) -> str:
        return str(
            self._public_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": object_key},
                ExpiresIn=self._expiry,
                HttpMethod="GET",
            )
        )


def get_object_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.object_storage)
