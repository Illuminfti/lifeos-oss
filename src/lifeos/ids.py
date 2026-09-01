"""Stable, sortable public IDs without a third-party dependency."""
from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    """Return a Crockford-base32 ULID-compatible 26-character identifier."""
    timestamp_ms = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    return _encode(timestamp_ms, 10) + _encode(randomness, 16)


def new_id(prefix: str) -> str:
    clean = prefix.strip().lower().replace("_", "-")
    if not clean or not clean.replace("-", "").isalnum():
        raise ValueError(f"invalid id prefix: {prefix!r}")
    return f"{clean}_{ulid()}"
