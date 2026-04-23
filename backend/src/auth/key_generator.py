"""API key generation and bcrypt verification."""

from __future__ import annotations

import secrets

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.

    Returns the full key, display prefix, and bcrypt hash. The full key must be
    shown once and never persisted.
    """
    random_part = secrets.token_urlsafe(24)
    full_key = f"sk-{random_part}"
    prefix = full_key[:10]
    key_hash = pwd_context.hash(full_key)
    return full_key, prefix, key_hash


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    """Verify a plain API key against its stored bcrypt hash."""
    try:
        return bool(pwd_context.verify(plain_key, hashed_key))
    except Exception:
        return False

