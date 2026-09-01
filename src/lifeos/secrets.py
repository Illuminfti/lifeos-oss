"""Secret custody ports.

The built-in backend is deliberately boring: an owner-only JSON file below
`.lifeos/`. It is not encrypted at rest and the CLI says so. The interface lets
installations replace it with an OS keychain or external secret manager without
changing connector code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from lifeos.config import atomic_write_text
from lifeos.errors import ConfigurationError


class SecretStore(ABC):
    @abstractmethod
    def put(self, payload: Mapping[str, Any], *, label: str = "connector") -> str:
        raise NotImplementedError

    @abstractmethod
    def get(self, ref: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update(self, ref: str, payload: Mapping[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, ref: str) -> bool:
        raise NotImplementedError


class FileSecretStore(SecretStore):
    """Owner-only local secret file.

    This backend protects against accidental publication and other local users
    on a correctly configured Unix host. It does not claim disk encryption.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        if not self.path.exists():
            self._write({"schema": "lifeos.secrets/v1", "items": {}})
        self._assert_mode()

    def _assert_mode(self) -> None:
        if os.name != "posix" or not self.path.exists():
            return
        mode = self.path.stat().st_mode & 0o777
        if mode & 0o077:
            raise ConfigurationError(
                f"secret store permissions are too broad ({oct(mode)}); expected 0o600"
            )

    def _read(self) -> dict[str, Any]:
        self._assert_mode()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"cannot read secret store: {exc}") from exc
        if payload.get("schema") != "lifeos.secrets/v1":
            raise ConfigurationError("unsupported secret store schema")
        if not isinstance(payload.get("items"), dict):
            raise ConfigurationError("malformed secret store")
        return payload

    def _write(self, payload: Mapping[str, Any]) -> None:
        atomic_write_text(
            self.path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )

    def put(self, payload: Mapping[str, Any], *, label: str = "connector") -> str:
        if not payload:
            raise ConfigurationError("refusing to store an empty secret payload")
        state = self._read()
        secret_id = f"sec_{uuid4().hex}"
        state["items"][secret_id] = {"label": label, "payload": dict(payload)}
        self._write(state)
        return f"secret://{secret_id}"

    def get(self, ref: str) -> dict[str, Any]:
        if ref.startswith("env://"):
            name = ref.removeprefix("env://")
            value = os.environ.get(name)
            if value is None:
                raise ConfigurationError(f"environment secret {name!r} is not set")
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {"value": value}
            if not isinstance(parsed, dict):
                raise ConfigurationError(f"environment secret {name!r} must be JSON object")
            return parsed
        if not ref.startswith("secret://"):
            raise ConfigurationError("unsupported secret reference")
        secret_id = ref.removeprefix("secret://")
        item = self._read()["items"].get(secret_id)
        if not item:
            raise ConfigurationError("secret reference not found")
        return dict(item["payload"])

    def update(self, ref: str, payload: Mapping[str, Any]) -> None:
        if not ref.startswith("secret://"):
            raise ConfigurationError("only local secret references can be updated")
        secret_id = ref.removeprefix("secret://")
        state = self._read()
        item = state["items"].get(secret_id)
        if not item:
            raise ConfigurationError("secret reference not found")
        item["payload"] = dict(payload)
        self._write(state)

    def delete(self, ref: str) -> bool:
        if ref.startswith("env://"):
            return False
        if not ref.startswith("secret://"):
            return False
        secret_id = ref.removeprefix("secret://")
        state = self._read()
        existed = secret_id in state["items"]
        state["items"].pop(secret_id, None)
        if existed:
            self._write(state)
        return existed
