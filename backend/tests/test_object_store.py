"""S3 transport error normalization.

Callers handle ObjectStoreError (the serving route turns it into a 503).
botocore splits its failures across two disjoint hierarchies — service errors
are ClientError, transport failures (DNS, refused connection, timeout) are
BotoCoreError — so a wrapper that catches only ClientError lets exactly the
real-outage case escape as an unhandled 500.
"""

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.object_store import ObjectNotFound, ObjectStoreError, S3ObjectStore


def _store() -> S3ObjectStore:
    return S3ObjectStore(
        endpoint_url="http://unreachable.test:8333",
        access_key="key",
        secret_key="secret",
        bucket="newsread-media",
    )


class _UnreachableClient:
    """Every call fails the way an unreachable endpoint does."""

    def __getattr__(self, name):
        def call(*args, **kwargs):
            raise EndpointConnectionError(endpoint_url="http://unreachable.test:8333")

        return call


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.get("k"),
        lambda s: s.put("k", b"bytes"),
        lambda s: s.put_if_absent("k", b"bytes"),
        lambda s: s.delete("k"),
        lambda s: s.exists("k"),
        lambda s: s.ensure_bucket(),
        lambda s: s.list_objects("prefix/", limit=10),
    ],
)
async def test_transport_failures_become_object_store_errors(operation):
    store = _store()
    store._client = _UnreachableClient()
    with pytest.raises(ObjectStoreError) as excinfo:
        await operation(store)
    # Named by exception class, since a transport failure carries no S3 code.
    assert "EndpointConnectionError" in str(excinfo.value)


async def test_missing_object_still_raises_not_found():
    """The 404 path must survive the widened handler — a missing object is a
    permanent miss, not an outage."""
    store = _store()

    class _MissingClient:
        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    store._client = _MissingClient()
    with pytest.raises(ObjectNotFound):
        await store.get("k")


async def test_service_errors_still_report_their_s3_code():
    store = _store()

    class _DeniedClient:
        def put_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")

    store._client = _DeniedClient()
    with pytest.raises(ObjectStoreError, match="AccessDenied"):
        await store.put("k", b"bytes")
