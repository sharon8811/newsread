"""Encryption at rest for per-user messaging-platform tokens.

Every other secret in the app is a server-wide env var; platform OAuth tokens
are the first per-user secrets stored in Postgres, so they are Fernet-encrypted
with NEWSREAD_TOKEN_ENCRYPTION_KEY. Rotating/losing the key invalidates all
stored connections (users just reconnect from settings).
"""

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_fernet: Fernet | None = None


class TokenCryptoError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.token_encryption_key)


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not settings.token_encryption_key:
            raise TokenCryptoError(
                "NEWSREAD_TOKEN_ENCRYPTION_KEY is not set; cannot store platform tokens"
            )
        try:
            _fernet = Fernet(settings.token_encryption_key.encode())
        except (ValueError, TypeError) as exc:
            raise TokenCryptoError(f"Invalid NEWSREAD_TOKEN_ENCRYPTION_KEY: {exc}")
    return _fernet


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise TokenCryptoError(
            "Stored token cannot be decrypted (encryption key changed?)"
        )
