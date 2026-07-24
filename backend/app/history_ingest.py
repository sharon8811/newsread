"""Owner-scoped persistence for validated browser-history objects."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .history_content import (
    CanonicalHistoryDocument,
    HistoryContentError,
    canonicalize_history_document,
    legacy_history_document,
    require_history_hash,
    validate_history_image,
)
from .history_storage import (
    EncryptedHistoryStorage,
    encrypted_history_storage_from_settings,
    history_object_key,
)
from .models import BrowserHistoryDocument, BrowserHistoryImage, User

# Extraction v2 is enabled only with its eager embedding and document-search
# consumers present, so a client can never upload content the UI cannot find.
HISTORY_CONTENT_CAPABILITY_REVISION = 2


class HistoryIngestError(ValueError):
    """Safe validation or ownership failure for one uploaded object."""


class HistoryQuotaExceeded(HistoryIngestError):
    """The owner's configured encrypted-history storage budget is exhausted."""


async def history_storage_used_bytes(
    session: AsyncSession,
    user_id: int,
) -> int:
    document_bytes = await session.scalar(
        select(func.coalesce(func.sum(BrowserHistoryDocument.byte_size), 0)).where(
            BrowserHistoryDocument.user_id == user_id
        )
    )
    image_bytes = await session.scalar(
        select(func.coalesce(func.sum(BrowserHistoryImage.byte_size), 0)).where(
            BrowserHistoryImage.user_id == user_id
        )
    )
    return int(document_bytes or 0) + int(image_bytes or 0)


async def _enforce_storage_quota(
    session: AsyncSession,
    *,
    user_id: int,
    additional_bytes: int,
) -> None:
    used = await history_storage_used_bytes(session, user_id)
    if used + max(0, additional_bytes) > settings.history_user_storage_max_bytes:
        raise HistoryQuotaExceeded("browser history storage quota exceeded")


async def _lock_history_owner(session: AsyncSession, user_id: int) -> None:
    # Serialize quota decisions per owner so concurrent uploads cannot each
    # observe the same remaining budget and oversubscribe it.
    owner_id = await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if owner_id is None:
        raise HistoryIngestError("history object owner does not exist")


class HistoryIngestService:
    def __init__(self, storage: EncryptedHistoryStorage):
        self.storage = storage

    async def ingest_document(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        claimed_hash: str,
        payload: bytes,
    ) -> BrowserHistoryDocument:
        try:
            require_history_hash(claimed_hash, label="content hash")
            canonical = canonicalize_history_document(payload)
        except HistoryContentError as exc:
            raise HistoryIngestError(str(exc)) from exc
        if canonical.content_hash != claimed_hash:
            raise HistoryIngestError("content hash does not match canonical document bytes")
        return await self.persist_document(session, user_id=user_id, canonical=canonical)

    async def ingest_legacy_document(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        text: str,
    ) -> BrowserHistoryDocument:
        try:
            canonical = legacy_history_document(text)
        except HistoryContentError as exc:
            raise HistoryIngestError(str(exc)) from exc
        return await self.persist_document(session, user_id=user_id, canonical=canonical)

    async def persist_document(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        canonical: CanonicalHistoryDocument,
    ) -> BrowserHistoryDocument:
        await _lock_history_owner(session, user_id)
        existing = await session.scalar(
            select(BrowserHistoryDocument).where(
                BrowserHistoryDocument.user_id == user_id,
                BrowserHistoryDocument.content_hash == canonical.content_hash,
            )
        )
        if existing is not None and existing.storage_status == "ready":
            return existing

        await _enforce_storage_quota(
            session,
            user_id=user_id,
            additional_bytes=(
                len(canonical.canonical_bytes) - (existing.byte_size if existing is not None else 0)
            ),
        )
        object_key = history_object_key(user_id, "document", canonical.content_hash)
        await self.storage.put(
            session,
            user_id=user_id,
            object_type="document",
            object_hash=canonical.content_hash,
            plaintext=canonical.compressed_bytes,
        )
        statement = pg_insert(BrowserHistoryDocument).values(
            user_id=user_id,
            content_hash=canonical.content_hash,
            object_key=object_key,
            storage_status="ready",
            byte_size=len(canonical.canonical_bytes),
            character_count=canonical.character_count,
            text_excerpt=canonical.text_excerpt,
            search_tsv=func.to_tsvector("simple", canonical.search_text),
            extraction_version=canonical.extraction_version,
        )
        document_id = await session.scalar(
            statement.on_conflict_do_update(
                index_elements=["user_id", "content_hash"],
                set_={
                    "object_key": statement.excluded.object_key,
                    "storage_status": "ready",
                    "byte_size": statement.excluded.byte_size,
                    "character_count": statement.excluded.character_count,
                    "text_excerpt": statement.excluded.text_excerpt,
                    "search_tsv": statement.excluded.search_tsv,
                    "extraction_version": statement.excluded.extraction_version,
                    "updated_at": func.now(),
                },
            ).returning(BrowserHistoryDocument.id)
        )
        return await session.get(BrowserHistoryDocument, document_id)

    async def ingest_image(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        claimed_hash: str,
        payload: bytes,
    ) -> BrowserHistoryImage:
        try:
            require_history_hash(claimed_hash, label="image hash")
            validated = validate_history_image(
                payload,
                max_bytes=settings.history_image_max_bytes,
            )
        except HistoryContentError as exc:
            raise HistoryIngestError(str(exc)) from exc
        if validated.image_hash != claimed_hash:
            raise HistoryIngestError("image hash does not match uploaded bytes")

        await _lock_history_owner(session, user_id)
        existing = await session.scalar(
            select(BrowserHistoryImage).where(
                BrowserHistoryImage.user_id == user_id,
                BrowserHistoryImage.image_hash == claimed_hash,
            )
        )
        if existing is not None and existing.storage_status == "ready":
            return existing

        await _enforce_storage_quota(
            session,
            user_id=user_id,
            additional_bytes=(
                validated.byte_size - (existing.byte_size if existing is not None else 0)
            ),
        )
        object_key = history_object_key(user_id, "image", claimed_hash)
        await self.storage.put(
            session,
            user_id=user_id,
            object_type="image",
            object_hash=claimed_hash,
            plaintext=payload,
        )
        statement = pg_insert(BrowserHistoryImage).values(
            user_id=user_id,
            image_hash=claimed_hash,
            object_key=object_key,
            storage_status="ready",
            format=validated.format,
            width=validated.width,
            height=validated.height,
            byte_size=validated.byte_size,
        )
        image_id = await session.scalar(
            statement.on_conflict_do_update(
                index_elements=["user_id", "image_hash"],
                set_={
                    "object_key": statement.excluded.object_key,
                    "storage_status": "ready",
                    "format": statement.excluded.format,
                    "width": statement.excluded.width,
                    "height": statement.excluded.height,
                    "byte_size": statement.excluded.byte_size,
                    "updated_at": func.now(),
                },
            ).returning(BrowserHistoryImage.id)
        )
        return await session.get(BrowserHistoryImage, image_id)


@lru_cache
def get_history_ingest_service() -> HistoryIngestService:
    return HistoryIngestService(encrypted_history_storage_from_settings())


def get_optional_history_ingest_service() -> HistoryIngestService | None:
    if not settings.browser_history_content_enabled or HISTORY_CONTENT_CAPABILITY_REVISION < 2:
        return None
    return get_history_ingest_service()
