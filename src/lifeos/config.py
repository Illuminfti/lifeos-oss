"""LifeOS brain configuration.

Configuration is operational state. It never becomes canonical knowledge and is
never included in GBrain imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Mapping

from lifeos.errors import ConfigurationError, UnsafePath

CONFIG_SCHEMA = "lifeos.config/v1"
DEFAULT_CONFIG = {
    "schema": CONFIG_SCHEMA,
    "brain_id": "local",
    "gbrain": {"command": "gbrain", "enabled": True},
    "kernel": {"max_chars": 4000, "max_results": 8},
    "ingest": {"max_attempts": 5, "lease_seconds": 60},
    "models": {"extractor": "deterministic"},
}


def atomic_write_text(path: Path, content: str, *, mode: int | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(content, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)
    if mode is not None:
        os.chmod(path, mode)


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)  # type: ignore[arg-type]
        else:
            merged[key] = value
    return merged


@dataclass(slots=True)
class BrainConfig:
    root: Path
    values: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_CONFIG))

    @property
    def state_dir(self) -> Path:
        return self.root / ".lifeos"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.sqlite"

    @property
    def secrets_path(self) -> Path:
        return self.state_dir / "secrets.json"

    @property
    def config_path(self) -> Path:
        return self.state_dir / "config.json"

    @property
    def raw_dir(self) -> Path:
        return self.root / "07-raw"

    @property
    def receipts_dir(self) -> Path:
        return self.state_dir / "receipts"

    @property
    def webhook_dir(self) -> Path:
        return self.state_dir / "webhooks"

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass
        payload = json.dumps(self.values, indent=2, sort_keys=True) + "\n"
        atomic_write_text(self.config_path, payload, mode=0o600)

    def get(self, *keys: str, default: Any = None) -> Any:
        value: Any = self.values
        for key in keys:
            if not isinstance(value, Mapping) or key not in value:
                return default
            value = value[key]
        return value

    def resolve_inside(self, relative: str | Path) -> Path:
        candidate = (self.root / relative).resolve()
        root = self.root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise UnsafePath(f"path escapes brain: {relative}") from exc
        return candidate


def load_config(brain: str | Path, *, require_initialized: bool = True) -> BrainConfig:
    root = Path(brain).expanduser().resolve()
    config_path = root / ".lifeos" / "config.json"
    if not config_path.exists():
        if require_initialized:
            raise ConfigurationError(
                f"{root} is not an initialized LifeOS brain; run `lifeos init {root}`"
            )
        return BrainConfig(root=root, values=deep_merge(DEFAULT_CONFIG, {}))
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read {config_path}: {exc}") from exc
    if raw.get("schema") != CONFIG_SCHEMA:
        raise ConfigurationError(f"unsupported config schema: {raw.get('schema')!r}")
    return BrainConfig(root=root, values=deep_merge(DEFAULT_CONFIG, raw))
