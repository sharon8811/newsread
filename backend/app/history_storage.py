"""Provider-neutral object storage for encrypted browser-history content."""

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, settings
from .history_crypto import (
    HistoryCryptoError,
    HistoryObjectType,
    MasterKeyring,
    decrypt_object,
    encrypt_object,
    generate_data_key,
    parse_object_header,
)
from .models import BrowserHistoryUserKey, User


class HistoryStorageError(Exception):
    """Base error for object-store failures safe to handle at service boundaries."""


class HistoryObjectNotFound(HistoryStorageError):
    pass


class ObjectStore(Protocol):
    async def ensure_bucket(self) -> None: ...

    async def exists(self, key: str) -> bool: ...

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
        def ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)
                return
            except ClientError as exc:
                if _error_code(exc) not in {"404", "NoSuchBucket", "NotFound"}:
                    raise
            kwargs: dict[str, object] = {"Bucket": self.bucket}
            if self.region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {
                    "LocationConstraint": self.region,
                }
            self._client.create_bucket(**kwargs)

        try:
            await anyio.to_thread.run_sync(ensure)
        except ClientError as exc:
            raise HistoryStorageError(
                f"could not ensure history bucket: {_error_code(exc)}"
            ) from exc

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
            raise HistoryStorageError(
                f"could not inspect history object: {_error_code(exc)}"
            ) from exc

    async def put_if_absent(self, key: str, data: bytes) -> bool:
        def put() -> bool:
            try:
                self._client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=io.BytesIO(data),
                    ContentLength=len(data),
                    ContentType="application/octet-stream",
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
            raise HistoryStorageError(
                f"could not store history object: {_error_code(exc)}"
            ) from exc

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            try:
                response = self._client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                    raise HistoryObjectNotFound(key) from exc
                raise
            body = response["Body"]
            try:
                return body.read()
            finally:
                body.close()

        try:
            return await anyio.to_thread.run_sync(read)
        except HistoryObjectNotFound:
            raise
        except ClientError as exc:
            raise HistoryStorageError(f"could not read history object: {_error_code(exc)}") from exc

    async def delete(self, key: str) -> None:
        try:
            await anyio.to_thread.run_sync(
                partial(self._client.delete_object, Bucket=self.bucket, Key=key)
            )
        except ClientError as exc:
            raise HistoryStorageError(
                f"could not delete history object: {_error_code(exc)}"
            ) from exc

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
            raise HistoryStorageError(
                f"could not list history objects: {_error_code(exc)}"
            ) from exc


class InMemoryObjectStore:
    """Deterministic object-store fake shared by unit and service tests."""

    def __init__(self, initial: Mapping[str, bytes] | None = None):
        self.objects = dict(initial or {})
        now = datetime.now(UTC)
        self.modified_at = {key: now for key in self.objects}
        self._lock = anyio.Lock()

    async def ensure_bucket(self) -> None:
        return None

    async def exists(self, key: str) -> bool:
        return key in self.objects

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
            raise HistoryObjectNotFound(key) from None

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.modified_at.pop(key, None)

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


@dataclass(frozen=True)
class StoredHistoryObject:
    object_key: str
    created: bool
    encrypted_bytes: int
    data_key_version: int


class HistoryKeyService:
    """Creates, wraps, and resolves versioned per-user data keys."""

    def __init__(self, keyring: MasterKeyring):
        self.keyring = keyring

    async def current_data_key(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> tuple[int, bytes]:
        # Serialize first-key creation per user without a process-local lock.
        owner = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if owner is None:
            raise HistoryStorageError("history object owner does not exist")
        row = await session.scalar(
            select(BrowserHistoryUserKey)
            .where(
                BrowserHistoryUserKey.user_id == user_id,
                BrowserHistoryUserKey.retired_at.is_(None),
            )
            .order_by(BrowserHistoryUserKey.data_key_version.desc())
            .limit(1)
        )
        if row is None:
            data_key_version = 1
            data_key = generate_data_key()
            wrapped, wrapping_version = self.keyring.wrap_data_key(
                data_key,
                user_id=user_id,
                data_key_version=data_key_version,
            )
            row = BrowserHistoryUserKey(
                user_id=user_id,
                data_key_version=data_key_version,
                wrapped_data_key=wrapped,
                wrap_alg="AES-256-GCM",
                wrapping_key_version=wrapping_version,
            )
            session.add(row)
            await session.flush()
            return data_key_version, data_key
        return row.data_key_version, self._unwrap(row)

    async def data_key(
        self,
        session: AsyncSession,
        user_id: int,
        data_key_version: int,
    ) -> bytes:
        row = await session.get(
            BrowserHistoryUserKey,
            {
                "user_id": user_id,
                "data_key_version": data_key_version,
            },
        )
        if row is None:
            raise HistoryStorageError("history data key is unavailable")
        return self._unwrap(row)

    async def rewrap_data_keys(
        self,
        session: AsyncSession,
        *,
        batch_size: int = 500,
    ) -> int:
        """Rewrap one locked batch without rewriting encrypted objects."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        rows = list(
            await session.scalars(
                select(BrowserHistoryUserKey)
                .where(BrowserHistoryUserKey.wrapping_key_version != self.keyring.current_version)
                .order_by(
                    BrowserHistoryUserKey.user_id,
                    BrowserHistoryUserKey.data_key_version,
                )
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
        )
        for row in rows:
            data_key = self._unwrap(row)
            wrapped, wrapping_version = self.keyring.wrap_data_key(
                data_key,
                user_id=row.user_id,
                data_key_version=row.data_key_version,
            )
            row.wrapped_data_key = wrapped
            row.wrapping_key_version = wrapping_version
        return len(rows)

    def _unwrap(self, row: BrowserHistoryUserKey) -> bytes:
        if row.wrap_alg != "AES-256-GCM":
            raise HistoryStorageError(f"unsupported history key wrap algorithm {row.wrap_alg}")
        try:
            return self.keyring.unwrap_data_key(
                row.wrapped_data_key,
                user_id=row.user_id,
                data_key_version=row.data_key_version,
                wrapping_key_version=row.wrapping_key_version,
            )
        except HistoryCryptoError as exc:
            raise HistoryStorageError(str(exc)) from exc


class EncryptedHistoryStorage:
    """Encrypts owner-scoped objects before delegating to an object store."""

    def __init__(self, object_store: ObjectStore, key_service: HistoryKeyService):
        self.object_store = object_store
        self.key_service = key_service

    async def put(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        object_type: HistoryObjectType,
        object_hash: str,
        plaintext: bytes,
    ) -> StoredHistoryObject:
        data_key_version, data_key = await self.key_service.current_data_key(session, user_id)
        ciphertext = encrypt_object(
            plaintext,
            data_key=data_key,
            data_key_version=data_key_version,
            object_type=object_type,
            user_id=user_id,
            object_hash=object_hash,
        )
        key = history_object_key(user_id, object_type, object_hash)
        created = await self.object_store.put_if_absent(key, ciphertext)
        return StoredHistoryObject(
            object_key=key,
            created=created,
            encrypted_bytes=len(ciphertext),
            data_key_version=data_key_version,
        )

    async def get(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        object_type: HistoryObjectType,
        object_hash: str,
        object_key: str,
    ) -> bytes:
        expected_key = history_object_key(user_id, object_type, object_hash)
        if object_key != expected_key:
            raise HistoryStorageError("history object key does not match its owner and hash")
        ciphertext = await self.object_store.get(object_key)
        try:
            header = parse_object_header(ciphertext)
            data_key = await self.key_service.data_key(
                session,
                user_id,
                header.data_key_version,
            )
            return decrypt_object(
                ciphertext,
                data_key=data_key,
                object_type=object_type,
                user_id=user_id,
                object_hash=object_hash,
            )
        except HistoryCryptoError as exc:
            raise HistoryStorageError(str(exc)) from exc

    async def delete(self, object_key: str) -> None:
        await self.object_store.delete(object_key)


def history_object_key(
    user_id: int,
    object_type: HistoryObjectType,
    object_hash: str,
) -> str:
    # encrypt_object performs the same strict context validation before writes;
    # this lightweight validation also protects read/delete key construction.
    if user_id < 1 or object_type not in {"document", "image"}:
        raise HistoryStorageError("invalid history object namespace")
    if len(object_hash) != 64 or any(char not in "0123456789abcdef" for char in object_hash):
        raise HistoryStorageError("invalid history object hash")
    plural = "documents" if object_type == "document" else "images"
    return f"users/{user_id}/history/{plural}/sha256/{object_hash[:2]}/{object_hash}"


def s3_object_store_from_settings(config: Settings = settings) -> S3ObjectStore:
    endpoint = config.object_store_endpoint.strip()
    if not endpoint.startswith(("http://", "https://")):
        scheme = "https" if config.object_store_secure else "http"
        endpoint = f"{scheme}://{endpoint}"
    if endpoint in {"http://", "https://"}:
        raise HistoryStorageError("NEWSREAD_OBJECT_STORE_ENDPOINT is not configured")
    if not config.object_store_access_key or not config.object_store_secret_key:
        raise HistoryStorageError("object-store credentials are not configured")
    return S3ObjectStore(
        endpoint_url=endpoint,
        access_key=config.object_store_access_key,
        secret_key=config.object_store_secret_key,
        bucket=config.object_store_bucket,
        region=config.object_store_region,
    )


def encrypted_history_storage_from_settings(
    config: Settings = settings,
) -> EncryptedHistoryStorage:
    try:
        keyring = MasterKeyring.from_config(
            current_key=config.history_encryption_master_key,
            current_version=config.history_encryption_wrapping_key_version,
            previous_keys_json=config.history_encryption_previous_master_keys,
        )
    except HistoryCryptoError as exc:
        raise HistoryStorageError(str(exc)) from exc
    return EncryptedHistoryStorage(
        s3_object_store_from_settings(config),
        HistoryKeyService(keyring),
    )


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "unknown"))
