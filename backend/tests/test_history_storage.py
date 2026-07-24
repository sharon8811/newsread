import base64
import hashlib

import pytest
from sqlalchemy import select

from app.history_crypto import MasterKeyring
from app.history_storage import (
    EncryptedHistoryStorage,
    HistoryKeyService,
    HistoryObjectNotFound,
    HistoryStorageError,
    InMemoryObjectStore,
    history_object_key,
)
from app.models import BrowserHistoryUserKey


def _storage():
    key = base64.b64encode(bytes(range(32))).decode()
    store = InMemoryObjectStore()
    key_service = HistoryKeyService(MasterKeyring.from_config(current_key=key, current_version=1))
    return EncryptedHistoryStorage(store, key_service), store


@pytest.mark.asyncio
async def test_encrypted_storage_round_trip_and_idempotent_put(session, users):
    user = await users.create()
    storage, store = _storage()
    plaintext = b'{"schema_version":1,"blocks":[{"text":"private article"}]}'
    object_hash = hashlib.sha256(plaintext).hexdigest()

    first = await storage.put(
        session,
        user_id=user.id,
        object_type="document",
        object_hash=object_hash,
        plaintext=plaintext,
    )
    stored_ciphertext = store.objects[first.object_key]
    second = await storage.put(
        session,
        user_id=user.id,
        object_type="document",
        object_hash=object_hash,
        plaintext=plaintext,
    )

    assert first.created is True
    assert second.created is False
    assert store.objects[first.object_key] == stored_ciphertext
    assert plaintext not in stored_ciphertext
    assert (
        await storage.get(
            session,
            user_id=user.id,
            object_type="document",
            object_hash=object_hash,
            object_key=first.object_key,
        )
        == plaintext
    )
    keys = (
        await session.scalars(
            select(BrowserHistoryUserKey).where(BrowserHistoryUserKey.user_id == user.id)
        )
    ).all()
    assert len(keys) == 1


@pytest.mark.asyncio
async def test_encrypted_storage_rejects_cross_owner_key(session, users):
    owner = await users.create()
    other = await users.create()
    storage, _ = _storage()
    plaintext = b"private"
    object_hash = hashlib.sha256(plaintext).hexdigest()
    stored = await storage.put(
        session,
        user_id=owner.id,
        object_type="document",
        object_hash=object_hash,
        plaintext=plaintext,
    )

    with pytest.raises(HistoryStorageError, match="does not match"):
        await storage.get(
            session,
            user_id=other.id,
            object_type="document",
            object_hash=object_hash,
            object_key=stored.object_key,
        )


@pytest.mark.asyncio
async def test_missing_encrypted_object_is_explicit(session, users):
    user = await users.create()
    storage, _ = _storage()
    object_hash = "a" * 64

    with pytest.raises(HistoryObjectNotFound):
        await storage.get(
            session,
            user_id=user.id,
            object_type="document",
            object_hash=object_hash,
            object_key=history_object_key(user.id, "document", object_hash),
        )


@pytest.mark.asyncio
async def test_data_keys_rewrap_without_rewriting_objects(session, users):
    user = await users.create()
    old_key = base64.b64encode(bytes([1]) * 32).decode()
    new_key = base64.b64encode(bytes([2]) * 32).decode()
    store = InMemoryObjectStore()
    old_storage = EncryptedHistoryStorage(
        store,
        HistoryKeyService(MasterKeyring.from_config(current_key=old_key, current_version=1)),
    )
    plaintext = b"private content"
    object_hash = hashlib.sha256(plaintext).hexdigest()
    stored = await old_storage.put(
        session,
        user_id=user.id,
        object_type="document",
        object_hash=object_hash,
        plaintext=plaintext,
    )
    await session.commit()
    ciphertext = store.objects[stored.object_key]

    rotated = EncryptedHistoryStorage(
        store,
        HistoryKeyService(
            MasterKeyring.from_config(
                current_key=new_key,
                current_version=2,
                previous_keys_json=f'{{"1":"{old_key}"}}',
            )
        ),
    )
    assert await rotated.key_service.rewrap_data_keys(session) == 1
    await session.commit()

    key = await session.scalar(select(BrowserHistoryUserKey))
    assert key.wrapping_key_version == 2
    assert store.objects[stored.object_key] == ciphertext
    assert (
        await rotated.get(
            session,
            user_id=user.id,
            object_type="document",
            object_hash=object_hash,
            object_key=stored.object_key,
        )
        == plaintext
    )


def test_history_object_keys_are_owner_scoped_and_strict():
    assert history_object_key(7, "image", "a" * 64) == (
        f"users/7/history/images/sha256/aa/{'a' * 64}"
    )
    with pytest.raises(HistoryStorageError):
        history_object_key(7, "document", "../not-a-hash")
