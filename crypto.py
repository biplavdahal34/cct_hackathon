"""Encryption at rest for user-supplied API keys.

Master key: KEY_ENCRYPTION_KEY — one or more Fernet keys, comma-separated,
newest first. Rotation = generate a new key, put it first, keep the old ones
until every row has been re-encrypted (see `flask rotate-api-keys`).
The key lives only in the process environment: never in .env inside the
image, never in docker-compose.yml, never in git.
"""
import os

from cryptography.fernet import Fernet, MultiFernet, InvalidToken  # noqa: F401 (re-exported)


def _keys() -> list:
    raw = os.environ.get("KEY_ENCRYPTION_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def is_configured() -> bool:
    return bool(_keys())


def _fernet() -> MultiFernet:
    keys = _keys()
    if not keys:
        raise RuntimeError("KEY_ENCRYPTION_KEY is not set — API-key storage is disabled.")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Raises cryptography.fernet.InvalidToken if the row was not encrypted
    with any of the configured keys (wrong/rotated-away master key)."""
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def rotate(token: str) -> str:
    """Re-encrypt a token under the newest key (no-op if already current)."""
    return _fernet().rotate(token.encode("ascii")).decode("ascii")


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")
