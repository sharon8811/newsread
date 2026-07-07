import pytest

from app import crypto
from app.config import settings


@pytest.fixture(autouse=True)
def _reset_fernet_cache():
    crypto._fernet = None
    yield
    crypto._fernet = None


def test_roundtrip():
    token = crypto.encrypt_token("xoxp-secret-token")
    assert token != "xoxp-secret-token"
    assert crypto.decrypt_token(token) == "xoxp-secret-token"


def test_is_configured():
    assert crypto.is_configured() is True


def test_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", "")
    assert crypto.is_configured() is False
    with pytest.raises(crypto.TokenCryptoError, match="not set"):
        crypto.encrypt_token("x")


def test_invalid_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", "not-a-fernet-key")
    with pytest.raises(crypto.TokenCryptoError, match="Invalid"):
        crypto.encrypt_token("x")


def test_decrypt_garbage_raises():
    with pytest.raises(crypto.TokenCryptoError, match="cannot be decrypted"):
        crypto.decrypt_token("gAAAAABnot-a-real-ciphertext")


def test_decrypt_after_key_change_raises(monkeypatch):
    stored = crypto.encrypt_token("token")
    crypto._fernet = None
    monkeypatch.setattr(
        settings,
        "token_encryption_key",
        "YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODk=",
    )
    with pytest.raises(crypto.TokenCryptoError, match="key changed"):
        crypto.decrypt_token(stored)
