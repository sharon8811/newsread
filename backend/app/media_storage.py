"""Public media objects: AI-generated article illustrations.

Unlike browser-history content these bytes are neither secret nor user-scoped
(the serving route is deliberately unauthenticated), so they are stored
unencrypted in their own bucket. Keys are content-addressed, which makes
re-uploading the same image a no-op and keeps a failed migration re-runnable.

The object store is required: a deployment that generates images without one
would have nowhere to put them, so both the writer and the serving route fail
loudly rather than falling back to the database.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache

from .config import settings
from .object_store import ObjectStore, s3_object_store_from_settings

logger = logging.getLogger(__name__)

GENERATED_IMAGE_PREFIX = "articles/generated-images/"


def generated_image_key(article_id: int, data: bytes) -> str:
    """`articles/generated-images/{article_id}/{sha256}` — the article id keeps
    an operator's bucket listing readable; the digest makes the write
    idempotent."""
    return f"{GENERATED_IMAGE_PREFIX}{article_id}/{hashlib.sha256(data).hexdigest()}"


@lru_cache
def get_media_store() -> ObjectStore:
    """The media bucket's client (one per process; boto3 clients are reusable
    and thread-safe for this usage)."""
    return s3_object_store_from_settings(bucket=settings.object_store_media_bucket)


async def ensure_media_bucket() -> None:
    """Create the media bucket if the endpoint doesn't have it yet, so the
    first generated image doesn't race bucket creation.

    Best-effort at startup: a deployment that never generates images shouldn't
    fail to boot over an object store it doesn't use. Generation and serving
    report the real error if it turns out to matter."""
    try:
        await get_media_store().ensure_bucket()
    except Exception as exc:
        logger.warning("Media bucket is unavailable (generated images will fail): %s", exc)
