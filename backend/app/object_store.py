"""S3-compatible object storage, shared by every feature that keeps bytes
outside Postgres.

Two callers today: encrypted browser-history content (history_storage) and
public generated article images (media_storage). They differ in bucket and in
what they wrap around these primitives — the transport is the same.
"""

from __future__ import annotations

import io
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Protocol

import anyio
import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import Settings, settings

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


class ObjectStoreError(Exception):
    """Base error for object-store failures safe to handle at service boundaries."""


class ObjectNotFound(ObjectStoreError):
    pass


class ObjectStore(Protocol):
    async def ensure_bucket(self) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def put(self, key: str, data: bytes, *, content_type: str = ...) -> None: ...

    async def put_if_absent(self, key: str, data: bytes) -> bool: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def list_objects(
        self,
        prefix: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> StoredObjectPage: ...


class S3ObjectStore:
    """Async facade over the mature synchronous Boto3 S3 client."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ):
        self.bucket = bucket
        self.region = region
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 4, "mode": "standard"},
            ),
        )

    async def ensure_bucket(self) -> None:
        try:
            await anyio.to_thread.run_sync(self.ensure_bucket_sync)
        except ClientError as exc:
            raise ObjectStoreError(f"could not ensure bucket: {_error_code(exc)}") from exc

    def ensure_bucket_sync(self) -> None:
        """The blocking half, also called from Alembic migrations."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as exc:
            if _error_code(exc) not in {"404", "NoSuchBucket", "NotFound"}:
                raise
        kwargs: dict[str, object] = {"Bucket": self.bucket}
        if self.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        self._client.create_bucket(**kwargs)

    async def exists(self, key: str) -> bool:
        def head() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as exc:
                if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                    return False
                raise

        try:
            return await anyio.to_thread.run_sync(head)
        except ClientError as exc:
            raise ObjectStoreError(f"could not inspect object: {_error_code(exc)}") from exc

    async def put(
        self, key: str, data: bytes, *, content_type: str = _DEFAULT_CONTENT_TYPE
    ) -> None:
        try:
            await anyio.to_thread.run_sync(
                partial(self.put_sync, key, data, content_type=content_type)
            )
        except ClientError as exc:
            raise ObjectStoreError(f"could not store object: {_error_code(exc)}") from exc

    def put_sync(self, key: str, data: bytes, *, content_type: str = _DEFAULT_CONTENT_TYPE) -> None:
        """The blocking half, also called from Alembic migrations."""
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=io.BytesIO(data),
            ContentLength=len(data),
            ContentType=content_type,
        )

    async def put_if_absent(self, key: str, data: bytes) -> bool:
        def put() -> bool:
            try:
                self._client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=io.BytesIO(data),
                    ContentLength=len(data),
                    ContentType=_DEFAULT_CONTENT_TYPE,
                    IfNoneMatch="*",
                )
                return True
            except ClientError as exc:
                if _error_code(exc) in {"PreconditionFailed", "412"}:
                    return False
                raise

        try:
            return await anyio.to_thread.run_sync(put)
        except ClientError as exc:
            raise ObjectStoreError(f"could not store object: {_error_code(exc)}") from exc

    def get_sync(self, key: str) -> bytes:
        """The blocking half, also called from Alembic migrations."""
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFound(key) from exc
            raise
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    async def get(self, key: str) -> bytes:
        try:
            return await anyio.to_thread.run_sync(self.get_sync, key)
        except ObjectNotFound:
            raise
        except ClientError as exc:
            raise ObjectStoreError(f"could not read object: {_error_code(exc)}") from exc

    async def delete(self, key: str) -> None:
        try:
            await anyio.to_thread.run_sync(
                partial(self._client.delete_object, Bucket=self.bucket, Key=key)
            )
        except ClientError as exc:
            raise ObjectStoreError(f"could not delete object: {_error_code(exc)}") from exc

    async def list_objects(
        self,
        prefix: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> StoredObjectPage:
        def collect() -> StoredObjectPage:
            kwargs: dict[str, object] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": limit,
            }
            if cursor:
                # StartAfter is stable even if this page's orphan keys are
                # deleted before the next sweep; continuation tokens need not
                # remain valid across mutations on every S3-compatible store.
                kwargs["StartAfter"] = cursor
            page = self._client.list_objects_v2(**kwargs)
            output: list[StoredObjectInfo] = []
            for item in page.get("Contents", []):
                modified_at = item["LastModified"]
                if modified_at.tzinfo is None:
                    modified_at = modified_at.replace(tzinfo=UTC)
                output.append(
                    StoredObjectInfo(
                        key=item["Key"],
                        byte_size=int(item.get("Size", 0)),
                        modified_at=modified_at,
                    )
                )
            return StoredObjectPage(
                objects=output,
                next_cursor=(output[-1].key if page.get("IsTruncated") and output else None),
            )

        try:
            return await anyio.to_thread.run_sync(collect)
        except ClientError as exc:
            raise ObjectStoreError(f"could not list objects: {_error_code(exc)}") from exc


class InMemoryObjectStore:
    """Deterministic object-store fake shared by unit and service tests."""

    def __init__(self, initial: Mapping[str, bytes] | None = None):
        self.objects = dict(initial or {})
        now = datetime.now(UTC)
        self.modified_at = {key: now for key in self.objects}
        self.content_types: dict[str, str] = {}
        self._lock = anyio.Lock()

    async def ensure_bucket(self) -> None:
        return None

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def put(
        self, key: str, data: bytes, *, content_type: str = _DEFAULT_CONTENT_TYPE
    ) -> None:
        async with self._lock:
            self.objects[key] = bytes(data)
            self.modified_at[key] = datetime.now(UTC)
            self.content_types[key] = content_type

    async def put_if_absent(self, key: str, data: bytes) -> bool:
        async with self._lock:
            if key in self.objects:
                return False
            self.objects[key] = bytes(data)
            self.modified_at[key] = datetime.now(UTC)
            return True

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError:
            raise ObjectNotFound(key) from None

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.modified_at.pop(key, None)
        self.content_types.pop(key, None)

    async def list_objects(
        self,
        prefix: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> StoredObjectPage:
        now = datetime.now(UTC)
        keys = [key for key in sorted(self.objects) if key.startswith(prefix)]
        start = bisect_right(keys, cursor) if cursor else 0
        selected = keys[start : start + limit]
        objects = [
            StoredObjectInfo(
                key=key,
                byte_size=len(self.objects[key]),
                modified_at=self.modified_at.get(key, now),
            )
            for key in selected
        ]
        return StoredObjectPage(
            objects=objects,
            next_cursor=selected[-1] if start + len(selected) < len(keys) else None,
        )


@dataclass(frozen=True)
class StoredObjectInfo:
    key: str
    byte_size: int
    modified_at: datetime


@dataclass(frozen=True)
class StoredObjectPage:
    objects: list[StoredObjectInfo]
    next_cursor: str | None


def s3_object_store_from_settings(
    config: Settings = settings,
    *,
    bucket: str | None = None,
) -> S3ObjectStore:
    """A client for `bucket` (default: the history bucket) on the configured
    endpoint. Raises when the deployment has no object store — every caller
    treats that as a hard misconfiguration rather than degrading."""
    endpoint = config.object_store_endpoint.strip()
    if not endpoint.startswith(("http://", "https://")):
        scheme = "https" if config.object_store_secure else "http"
        endpoint = f"{scheme}://{endpoint}"
    if endpoint in {"http://", "https://"}:
        raise ObjectStoreError("NEWSREAD_OBJECT_STORE_ENDPOINT is not configured")
    if not config.object_store_access_key or not config.object_store_secret_key:
        raise ObjectStoreError("object-store credentials are not configured")
    return S3ObjectStore(
        endpoint_url=endpoint,
        access_key=config.object_store_access_key,
        secret_key=config.object_store_secret_key,
        bucket=bucket or config.object_store_bucket,
        region=config.object_store_region,
    )


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "unknown"))
