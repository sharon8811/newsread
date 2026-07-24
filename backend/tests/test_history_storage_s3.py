import os
import uuid

import pytest

from app.history_storage import HistoryObjectNotFound, S3ObjectStore

S3_ENDPOINT = os.getenv("NEWSREAD_TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(
    not S3_ENDPOINT,
    reason="set NEWSREAD_TEST_S3_ENDPOINT to run the S3-compatible storage test",
)


@pytest.mark.asyncio
async def test_s3_compatible_object_store_contract():
    store = S3ObjectStore(
        endpoint_url=S3_ENDPOINT or "",
        access_key=os.getenv("NEWSREAD_TEST_S3_ACCESS_KEY", "newsread-seaweedfs"),
        secret_key=os.getenv(
            "NEWSREAD_TEST_S3_SECRET_KEY",
            "newsread-seaweedfs-dev-secret",
        ),
        bucket=os.getenv("NEWSREAD_TEST_S3_BUCKET", "newsread-history"),
    )
    key = f"integration-tests/{uuid.uuid4().hex}"
    payload = b"encrypted-object-placeholder"

    await store.ensure_bucket()
    assert await store.put_if_absent(key, payload) is True
    try:
        assert await store.exists(key) is True
        assert await store.get(key) == payload
        assert await store.put_if_absent(key, b"must-not-overwrite") is False
        assert await store.get(key) == payload
    finally:
        await store.delete(key)

    assert await store.exists(key) is False
    with pytest.raises(HistoryObjectNotFound):
        await store.get(key)
