import base64

import pytest

from app.history_crypto import (
    HistoryCryptoError,
    MasterKeyring,
    decrypt_object,
    encrypt_object,
    generate_data_key,
    parse_object_header,
)


def _encoded(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode()


def test_object_encryption_round_trip_and_header():
    data_key = generate_data_key()
    plaintext = b'{"blocks":[{"id":"b0001","text":"private"}]}'
    object_hash = "a" * 64

    ciphertext = encrypt_object(
        plaintext,
        data_key=data_key,
        data_key_version=7,
        object_type="document",
        user_id=42,
        object_hash=object_hash,
    )

    assert plaintext not in ciphertext
    assert parse_object_header(ciphertext).data_key_version == 7
    assert (
        decrypt_object(
            ciphertext,
            data_key=data_key,
            object_type="document",
            user_id=42,
            object_hash=object_hash,
        )
        == plaintext
    )


@pytest.mark.parametrize(
    ("object_type", "user_id", "object_hash"),
    [
        ("image", 42, "a" * 64),
        ("document", 43, "a" * 64),
        ("document", 42, "b" * 64),
    ],
)
def test_object_encryption_binds_authenticated_context(object_type, user_id, object_hash):
    data_key = generate_data_key()
    ciphertext = encrypt_object(
        b"private",
        data_key=data_key,
        data_key_version=1,
        object_type="document",
        user_id=42,
        object_hash="a" * 64,
    )

    with pytest.raises(HistoryCryptoError, match="authentication failed"):
        decrypt_object(
            ciphertext,
            data_key=data_key,
            object_type=object_type,
            user_id=user_id,
            object_hash=object_hash,
        )


@pytest.mark.parametrize(
    "ciphertext",
    [
        b"",
        b"wrong magic" + bytes(64),
        b"NRHO" + bytes([99]) + bytes(64),
    ],
)
def test_invalid_object_headers_fail_closed(ciphertext):
    with pytest.raises(HistoryCryptoError):
        parse_object_header(ciphertext)


def test_wrapped_data_key_survives_master_key_rotation():
    old = MasterKeyring.from_config(current_key=_encoded(1), current_version=1)
    data_key = generate_data_key()
    wrapped, wrapping_version = old.wrap_data_key(
        data_key,
        user_id=9,
        data_key_version=3,
    )

    rotated = MasterKeyring.from_config(
        current_key=_encoded(2),
        current_version=2,
        previous_keys_json=f'{{"1":"{_encoded(1)}"}}',
    )
    unwrapped = rotated.unwrap_data_key(
        wrapped,
        user_id=9,
        data_key_version=3,
        wrapping_key_version=wrapping_version,
    )
    rewrapped, new_wrapping_version = rotated.wrap_data_key(
        unwrapped,
        user_id=9,
        data_key_version=3,
    )

    assert new_wrapping_version == 2
    assert rewrapped != wrapped
    assert (
        rotated.unwrap_data_key(
            rewrapped,
            user_id=9,
            data_key_version=3,
            wrapping_key_version=2,
        )
        == data_key
    )


def test_wrapped_data_key_is_bound_to_owner_and_version():
    keyring = MasterKeyring.from_config(current_key=_encoded(1), current_version=1)
    wrapped, wrapping_version = keyring.wrap_data_key(
        generate_data_key(),
        user_id=9,
        data_key_version=3,
    )

    with pytest.raises(HistoryCryptoError, match="authentication failed"):
        keyring.unwrap_data_key(
            wrapped,
            user_id=10,
            data_key_version=3,
            wrapping_key_version=wrapping_version,
        )
