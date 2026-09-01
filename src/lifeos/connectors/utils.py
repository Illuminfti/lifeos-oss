from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import re
from typing import Any


def to_iso(value: Any) -> str:
    if value in (None, ""):
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if isinstance(value, (int, float)):
        number = float(value)
        number = number / 1000 if number > 10_000_000_000 else number
        return datetime.fromtimestamp(number, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    text = str(value).strip()
    if text.isdigit():
        return to_iso(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except ValueError:
        return text


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def compact_json(value: Any, limit: int = 20_000) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit] + "…"


def verify_hex_hmac(secret: str, body: bytes, supplied: str, prefix: str = "") -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied.removeprefix(prefix))


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"
