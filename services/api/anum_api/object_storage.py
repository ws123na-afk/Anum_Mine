"""S3-compatible object storage client (see docs/data-architecture.md "Now" scope).

Wraps a boto3 S3 client configured from `anum_api.settings` so attachments,
exports, transcripts, and other large payloads can be stored outside
Postgres, with database rows referencing them by bucket/key/checksum/size
(see `anum_api.schemas.FileObject`).

Unset `object_storage_endpoint_url` means the feature is disabled for this
deployment: `build_object_storage_client()` returns `None` and calling code
is expected to turn that into an HTTP 503, rather than silently defaulting
to some bucket nobody provisioned.
"""

from __future__ import annotations

import hashlib
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .settings import settings


class ObjectStorageNotConfiguredError(Exception):
    """Raised when object storage is used before an endpoint is configured."""


class ObjectStorageIntegrityError(Exception):
    """Raised when an uploaded object's stored size doesn't match what was sent."""


_NOT_FOUND_ERROR_CODES = {"404", "NoSuchKey", "NotFound"}


class ObjectStorageClient:
    """Thin wrapper around a boto3 S3 client for an S3-compatible backend (e.g. MinIO)."""

    def __init__(
        self,
        *,
        endpoint_url: str | None,
        bucket: str,
        region: str,
        access_key: str | None,
        secret_key: str | None,
    ) -> None:
        if not endpoint_url:
            raise ObjectStorageNotConfiguredError(
                "Object storage is not configured: ANUM_OBJECT_STORAGE_ENDPOINT_URL is unset"
            )

        self.bucket = bucket
        self._region = region
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotoConfig(signature_version="s3v4"),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Create the configured bucket if it doesn't already exist.

        Mirrors the bootstrap a real deployment needs against a fresh
        MinIO/S3 bucket, without failing if another process (or an earlier
        call) already created it.
        """

        try:
            self._client.head_bucket(Bucket=self.bucket)
            return
        except ClientError:
            pass

        create_kwargs: dict[str, Any] = {"Bucket": self.bucket}
        if self._region and self._region != "us-east-1":
            create_kwargs["CreateBucketConfiguration"] = {
                "LocationConstraint": self._region
            }
        try:
            self._client.create_bucket(**create_kwargs)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise

    def upload(self, key: str, data: bytes, content_type: str) -> dict[str, Any]:
        """Upload `data` under `key` and return checksum/size/etag metadata.

        The sha256 checksum is computed locally rather than trusted from
        S3's ETag (which is a plain MD5 only for single-part uploads and
        something else entirely for multipart ones). After the write, a
        HEAD request confirms the stored size matches what was sent, as a
        cheap integrity check against a silently truncated upload.
        """

        checksum_sha256 = hashlib.sha256(data).hexdigest()
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": checksum_sha256},
        )

        head = self._client.head_object(Bucket=self.bucket, Key=key)
        stored_size = head["ContentLength"]
        if stored_size != len(data):
            raise ObjectStorageIntegrityError(
                f"Uploaded object {key!r} has size {stored_size}, expected {len(data)}"
            )

        return {
            "bucket": self.bucket,
            "key": key,
            "checksum_sha256": checksum_sha256,
            "size_bytes": stored_size,
            "content_type": content_type,
            "etag": head.get("ETag", "").strip('"'),
        }

    def download(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def generate_presigned_download_url(self, key: str, expires_in_seconds: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in_seconds,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def head(self, key: str) -> dict[str, Any] | None:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in _NOT_FOUND_ERROR_CODES:
                return None
            raise
        return {
            "size_bytes": response["ContentLength"],
            "content_type": response.get("ContentType"),
            "etag": response.get("ETag", "").strip('"'),
        }


def build_object_storage_client() -> "ObjectStorageClient | None":
    """Build an `ObjectStorageClient` from settings, or `None` if unconfigured.

    The caller (a FastAPI dependency, not this module) is responsible for
    turning `None` into an HTTP 503 for endpoints that need object storage.
    """

    if not settings.object_storage_endpoint_url:
        return None
    return ObjectStorageClient(
        endpoint_url=settings.object_storage_endpoint_url,
        bucket=settings.object_storage_bucket,
        region=settings.object_storage_region,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
    )
