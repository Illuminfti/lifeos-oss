"""Resolve secret handles. Raw literal credentials are never accepted as config."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any

from lifeos.errors import AuthenticationRequired, ConfigurationError


class SecretResolver:
    def resolve_text(self, ref: str | None) -> str:
        if not ref:
            raise AuthenticationRequired("secret_ref is required")
        if ref.startswith("env:"):
            name = ref[4:]
            value = os.environ.get(name)
            if not name or value is None:
                raise AuthenticationRequired(f"environment secret unavailable: {name or '<empty>'}")
            return value
        if ref.startswith("file:"):
            path = Path(ref[5:]).expanduser()
            if not path.is_absolute():
                raise ConfigurationError("file secret refs must be absolute")
            if not path.is_file():
                raise AuthenticationRequired(f"secret file does not exist: {path}")
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ConfigurationError(f"secret file must be mode 0600 or stricter: {path}")
            return path.read_text(encoding="utf-8").strip()
        raise ConfigurationError("secret_ref must start env: or file:")

    def resolve_json(self, ref: str | None) -> dict[str, Any]:
        try:
            value = json.loads(self.resolve_text(ref))
        except json.JSONDecodeError as exc:
            raise ConfigurationError("secret_ref must resolve to a JSON object") from exc
        if not isinstance(value, dict):
            raise ConfigurationError("secret_ref must resolve to a JSON object")
        return value
