"""Immutable raw evidence manifests."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from lifeos.config import BrainConfig, atomic_write_text
from lifeos.contracts import CaptureEvent
from lifeos.wiki import slugify


class RawStore:
    def __init__(self, config: BrainConfig):
        self.config = config

    def put(self, event: CaptureEvent) -> str:
        occurred = event.occurred_at.replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(occurred)
            year, month, day = moment.strftime("%Y"), moment.strftime("%m"), moment.strftime("%d")
        except ValueError:
            year, month, day = "unknown", "unknown", "unknown"
        connector = slugify(event.connector_id.removeprefix("org.lifeos."))
        relative = Path("07-raw") / connector / year / month / day / f"{event.event_id}.json"
        destination = self.config.resolve_inside(relative)
        if not destination.exists():
            atomic_write_text(
                destination,
                json.dumps(event.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                mode=0o600,
            )
        return relative.as_posix()

    def delete_for_connection(self, connection_id: str) -> int:
        count = 0
        root = self.config.raw_dir
        if not root.exists():
            return 0
        for path in root.rglob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if value.get("connection_id") == connection_id:
                path.unlink(missing_ok=True)
                count += 1
        for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        return count
