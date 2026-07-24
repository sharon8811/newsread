"""Envelope encryption primitives for private browser-history objects.

Object storage only receives ciphertext. A random per-user data key encrypts
documents and images with AES-256-GCM; versioned master keys wrap those data
keys in Postgres. Object type, owner, and hash are authenticated so ciphertext
cannot be replayed in a different namespace.
"""

import base64
import binascii
import json
import os
import re
import struct
from dataclasses import dataclass
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HistoryObjectType = Literal["document", "image"]

DATA_KEY_BYTES = 32
NONCE_BYTES = 12
WRAP_ALGORITHM = "AES-256-GCM"

_OBJECT_MAGIC = b"NRHO"
_OBJECT_FORMAT_VERSION = 1
_OBJECT_HEADER = struct.Struct(">4sBI12s")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class HistoryCryptoError(Exception):
    """Safe base error for invalid keys, ciphertext, or authenticated context."""


@dataclass(frozen=True)
class EncryptedObjectHeader:
    format_version: int
    data_key_version: int
    nonce: bytes


def _decode_key(value: str, *, label: str) -> bytes:
    try:
        key = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise HistoryCryptoError(f"{label} must be valid base64") from exc
    if len(key) != DATA_KEY_BYTES:
        raise HistoryCryptoError(f"{label} must decode to 32 bytes")
    return key


def _positive_version(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise HistoryCryptoError(f"{label} must be a positive integer")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise HistoryCryptoError(f"{label} must be a positive integer") from exc
    if version < 1 or version > 2_147_483_647:
        raise HistoryCryptoError(f"{label} must be a positive integer")
    return version


class MasterKeyring:
    """Versioned AES keys used only to wrap per-user data keys."""

    def __init__(self, keys: dict[int, bytes], current_version: int):
        current_version = _positive_version(current_version, label="current wrapping key version")
        if current_version not in keys:
            raise HistoryCryptoError("current wrapping key version is missing from the keyring")
        if any(len(key) != DATA_KEY_BYTES for key in keys.values()):
            raise HistoryCryptoError("every wrapping key must be 32 bytes")
        self._keys = dict(keys)
        self.current_version = current_version

    @classmethod
    def from_config(
        cls,
        *,
        current_key: str,
        current_version: int,
        previous_keys_json: str = "",
    ) -> "MasterKeyring":
        current_version = _positive_version(current_version, label="current wrapping key version")
        keys: dict[int, bytes] = {}
        if previous_keys_json:
            try:
                raw = json.loads(previous_keys_json)
            except json.JSONDecodeError as exc:
                raise HistoryCryptoError(
                    "NEWSREAD_HISTORY_ENCRYPTION_PREVIOUS_MASTER_KEYS must be a JSON object"
                ) from exc
            if not isinstance(raw, dict):
                raise HistoryCryptoError(
                    "NEWSREAD_HISTORY_ENCRYPTION_PREVIOUS_MASTER_KEYS must be a JSON object"
                )
            for version, encoded in raw.items():
                parsed_version = _positive_version(version, label="wrapping key version")
                if not isinstance(encoded, str):
                    raise HistoryCryptoError("wrapping keys must be base64 strings")
                keys[parsed_version] = _decode_key(
                    encoded,
                    label=f"wrapping key version {parsed_version}",
                )
        if not current_key:
            raise HistoryCryptoError("NEWSREAD_HISTORY_ENCRYPTION_MASTER_KEY is not set")
        keys[current_version] = _decode_key(
            current_key,
            label="NEWSREAD_HISTORY_ENCRYPTION_MASTER_KEY",
        )
        return cls(keys, current_version)

    def wrap_data_key(
        self,
        data_key: bytes,
        *,
        user_id: int,
        data_key_version: int,
    ) -> tuple[bytes, int]:
        if len(data_key) != DATA_KEY_BYTES:
            raise HistoryCryptoError("data key must be 32 bytes")
        nonce = os.urandom(NONCE_BYTES)
        aad = _data_key_aad(user_id, data_key_version)
        wrapped = AESGCM(self._keys[self.current_version]).encrypt(nonce, data_key, aad)
        return nonce + wrapped, self.current_version

    def unwrap_data_key(
        self,
        wrapped_data_key: bytes,
        *,
        user_id: int,
        data_key_version: int,
        wrapping_key_version: int,
    ) -> bytes:
        key = self._keys.get(wrapping_key_version)
        if key is None:
            raise HistoryCryptoError(
                f"wrapping key version {wrapping_key_version} is not configured"
            )
        if len(wrapped_data_key) < NONCE_BYTES + 16:
            raise HistoryCryptoError("wrapped data key is truncated")
        nonce = wrapped_data_key[:NONCE_BYTES]
        ciphertext = wrapped_data_key[NONCE_BYTES:]
        try:
            data_key = AESGCM(key).decrypt(
                nonce,
                ciphertext,
                _data_key_aad(user_id, data_key_version),
            )
        except InvalidTag:
            raise HistoryCryptoError("wrapped data key authentication failed") from None
        if len(data_key) != DATA_KEY_BYTES:
            raise HistoryCryptoError("unwrapped data key has an invalid length")
        return data_key


def generate_data_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def parse_object_header(ciphertext: bytes) -> EncryptedObjectHeader:
    if len(ciphertext) < _OBJECT_HEADER.size + 16:
        raise HistoryCryptoError("encrypted history object is truncated")
    magic, format_version, data_key_version, nonce = _OBJECT_HEADER.unpack_from(ciphertext)
    if magic != _OBJECT_MAGIC:
        raise HistoryCryptoError("encrypted history object has invalid magic")
    if format_version != _OBJECT_FORMAT_VERSION:
        raise HistoryCryptoError(f"unsupported encrypted history object version {format_version}")
    if data_key_version < 1:
        raise HistoryCryptoError("encrypted history object has an invalid data key version")
    return EncryptedObjectHeader(
        format_version=format_version,
        data_key_version=data_key_version,
        nonce=nonce,
    )


def encrypt_object(
    plaintext: bytes,
    *,
    data_key: bytes,
    data_key_version: int,
    object_type: HistoryObjectType,
    user_id: int,
    object_hash: str,
) -> bytes:
    _validate_object_context(object_type, user_id, object_hash)
    data_key_version = _positive_version(data_key_version, label="data key version")
    if len(data_key) != DATA_KEY_BYTES:
        raise HistoryCryptoError("data key must be 32 bytes")
    nonce = os.urandom(NONCE_BYTES)
    header = _OBJECT_HEADER.pack(
        _OBJECT_MAGIC,
        _OBJECT_FORMAT_VERSION,
        data_key_version,
        nonce,
    )
    encrypted = AESGCM(data_key).encrypt(
        nonce,
        plaintext,
        _object_aad(object_type, user_id, object_hash),
    )
    return header + encrypted


def decrypt_object(
    ciphertext: bytes,
    *,
    data_key: bytes,
    object_type: HistoryObjectType,
    user_id: int,
    object_hash: str,
) -> bytes:
    _validate_object_context(object_type, user_id, object_hash)
    header = parse_object_header(ciphertext)
    if len(data_key) != DATA_KEY_BYTES:
        raise HistoryCryptoError("data key must be 32 bytes")
    try:
        return AESGCM(data_key).decrypt(
            header.nonce,
            ciphertext[_OBJECT_HEADER.size :],
            _object_aad(object_type, user_id, object_hash),
        )
    except InvalidTag:
        raise HistoryCryptoError("encrypted history object authentication failed") from None


def _validate_object_context(
    object_type: HistoryObjectType,
    user_id: int,
    object_hash: str,
) -> None:
    if object_type not in {"document", "image"}:
        raise HistoryCryptoError("history object type must be document or image")
    if user_id < 1:
        raise HistoryCryptoError("history object user id must be positive")
    if not _HASH_RE.fullmatch(object_hash):
        raise HistoryCryptoError("history object hash must be 64 lowercase hex characters")


def _data_key_aad(user_id: int, data_key_version: int) -> bytes:
    if user_id < 1:
        raise HistoryCryptoError("history key user id must be positive")
    version = _positive_version(data_key_version, label="data key version")
    return f"newsread-history-data-key-v1\0{user_id}\0{version}".encode()


def _object_aad(
    object_type: HistoryObjectType,
    user_id: int,
    object_hash: str,
) -> bytes:
    return f"newsread-history-object-v1\0{object_type}\0{user_id}\0{object_hash}".encode()
