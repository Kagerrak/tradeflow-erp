from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.client import BaseClient  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from fastapi import Request

from tradeflow_api.config import Settings


@dataclass(frozen=True, slots=True)
class StoredObjectMetadata:
    content_type: str
    sha256: str
    size_bytes: int


class ObjectStorage(Protocol):
    async def ensure_bucket(self) -> None: ...

    def signed_put_url(
        self,
        *,
        content_type: str,
        object_key: str,
        sha256: str,
    ) -> str: ...

    async def head(self, object_key: str) -> StoredObjectMetadata: ...


class S3ObjectStorage:
    def __init__(self, settings: Settings) -> None:
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

    async def ensure_bucket(self) -> None:
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        except ClientError as error:
            status = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status not in {403, 404}:
                raise
            await asyncio.to_thread(self._client.create_bucket, Bucket=self._bucket)

    def signed_put_url(
        self,
        *,
        content_type: str,
        object_key: str,
        sha256: str,
    ) -> str:
        return str(
            self._public_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": object_key,
                    "ContentType": content_type,
                    "Metadata": {"sha256": sha256},
                },
                ExpiresIn=self._expiry,
                HttpMethod="PUT",
            )
        )

    async def head(self, object_key: str) -> StoredObjectMetadata:
        response = await asyncio.to_thread(
            self._client.head_object,
            Bucket=self._bucket,
            Key=object_key,
        )
        metadata = response.get("Metadata", {})
        return StoredObjectMetadata(
            content_type=str(response.get("ContentType", "")),
            sha256=str(metadata.get("sha256", "")),
            size_bytes=int(response.get("ContentLength", 0)),
        )


def get_object_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.object_storage)
