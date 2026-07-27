"""Media-bucket keys and the startup bucket check."""

import pytest

from app import media_storage
from app.object_store import ObjectStoreError, s3_object_store_from_settings


def test_generated_image_key_is_content_addressed():
    key = media_storage.generated_image_key(7, b"png-bytes")
    assert key.startswith("articles/generated-images/7/")
    # Same bytes -> same key, so re-uploading (a retried generation, a re-run
    # migration) overwrites in place instead of accumulating copies.
    assert key == media_storage.generated_image_key(7, b"png-bytes")
    assert key != media_storage.generated_image_key(7, b"other-bytes")
    assert key != media_storage.generated_image_key(8, b"png-bytes")


def test_generated_image_keys_stay_out_of_the_history_namespace():
    """The history GC sweeps `users/` and deletes what its own tables don't
    reference — media keys must never land under it."""
    from app.history_gc import _MANAGED_OBJECT_KEY

    key = media_storage.generated_image_key(1, b"x")
    assert not key.startswith("users/")
    assert _MANAGED_OBJECT_KEY.fullmatch(key) is None


def test_media_store_uses_its_own_bucket(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "object_store_endpoint", "http://seaweed.test:8333")
    monkeypatch.setattr(settings, "object_store_access_key", "key")
    monkeypatch.setattr(settings, "object_store_secret_key", "secret")
    monkeypatch.setattr(settings, "object_store_bucket", "newsread-history")
    monkeypatch.setattr(settings, "object_store_media_bucket", "newsread-media")

    store = s3_object_store_from_settings(bucket=settings.object_store_media_bucket)
    assert store.bucket == "newsread-media"
    assert s3_object_store_from_settings().bucket == "newsread-history"


def test_object_store_factory_requires_configuration(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "object_store_endpoint", "")
    with pytest.raises(ObjectStoreError, match="not configured"):
        s3_object_store_from_settings()


async def test_ensure_media_bucket_warns_instead_of_failing_startup(monkeypatch, caplog):
    """An install that never generates images shouldn't fail to boot over an
    object store it doesn't use."""

    class Broken:
        async def ensure_bucket(self):
            raise ObjectStoreError("could not ensure bucket: ConnectionError")

    monkeypatch.setattr(media_storage, "get_media_store", lambda: Broken())
    with caplog.at_level("WARNING"):
        await media_storage.ensure_media_bucket()
    assert "Media bucket is unavailable" in caplog.text
