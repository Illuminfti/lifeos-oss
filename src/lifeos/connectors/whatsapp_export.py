"""WhatsApp user-export importer.

This connector does not scrape WhatsApp Web and does not claim live personal
account access. It imports the text exports WhatsApp users can create.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import ConfigurationError

PATTERNS = (
    re.compile(r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\s+-\s+(?P<sender>[^:]+):\s?(?P<text>.*)$"),
    re.compile(r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\]\s+(?P<sender>[^:]+):\s?(?P<text>.*)$"),
    re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s+-\s+(?P<sender>[^:]+):\s?(?P<text>.*)$"),
)


@dataclass(slots=True)
class ExportMessage:
    line_number: int
    occurred_at: str
    sender: str
    text: str


def _parse_time(date_text: str, time_text: str) -> str:
    value = f"{date_text} {time_text.strip().upper().replace('.', '')}"
    formats = (
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M",
        "%d/%m/%Y %I:%M %p",
        "%m/%d/%Y %I:%M %p",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
        except ValueError:
            continue
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_export(text: str) -> list[ExportMessage]:
    messages: list[ExportMessage] = []
    current: ExportMessage | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = next((pattern.match(line.lstrip("\ufeff")) for pattern in PATTERNS if pattern.match(line.lstrip("\ufeff"))), None)
        if match:
            if current is not None:
                messages.append(current)
            current = ExportMessage(
                line_number=number,
                occurred_at=_parse_time(match.group("date"), match.group("time")),
                sender=match.group("sender").strip(),
                text=match.group("text").strip(),
            )
        elif current is not None:
            current.text += "\n" + line
    if current is not None:
        messages.append(current)
    return messages


class WhatsAppExportConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.whatsapp-export",
        display_name="WhatsApp chat export",
        source_classes=("message", "thread", "person", "attachment_reference"),
        capabilities=("backfill", "incremental_sync", "revoke", "purge"),
        auth_modes=("filesystem_consent",),
        custody="local",
        implementation_status="working",
        notes="Imports user-created exports. It does not claim personal-account API history.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        raw = request.get("path") or request.get("export_path")
        if not raw:
            raise ConfigurationError("whatsapp-export requires `path`")
        path = Path(str(raw)).expanduser().resolve()
        if not path.exists() or (not path.is_file() and not path.is_dir()):
            raise ConfigurationError(f"export path does not exist: {path}")
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={"path": str(path), "owner_name": str(request.get("owner_name", ""))},
            granted_scopes=("filesystem:read",),
        )

    def _files(self, connection: Connection) -> Iterable[Path]:
        path = Path(str(connection.settings["path"]))
        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.txt"))

    def _batch(self, connection: Connection, checkpoint: Mapping[str, Any]) -> SyncBatch:
        previous = {str(k): str(v) for k, v in dict(checkpoint.get("files", {})).items()}
        current: dict[str, str] = {}
        events: list[CaptureEvent] = []
        root = Path(str(connection.settings["path"]))
        for file in self._files(connection):
            data = file.read_bytes()
            digest = "sha256:" + sha256(data).hexdigest()
            key = file.name if root.is_file() else file.relative_to(root).as_posix()
            current[key] = digest
            if previous.get(key) == digest:
                continue
            messages = parse_export(data.decode("utf-8", errors="replace"))
            thread_id = "whatsapp-export:" + key
            for message in messages:
                record = f"{key}:{message.line_number}"
                source_revision = "sha256:" + sha256(
                    f"{message.occurred_at}\0{message.sender}\0{message.text}".encode("utf-8")
                ).hexdigest()
                media_omitted = "media omitted" in message.text.lower()
                events.append(
                    CaptureEvent.create(
                        connector_id=self.manifest.id,
                        connection_id=connection.connection_id,
                        source_record_id=record,
                        source_revision=source_revision,
                        source_thread_id=thread_id,
                        kind="message.created",
                        occurred_at=message.occurred_at,
                        actors=(Actor(provider_ref=f"export:{message.sender}", display_name=message.sender),),
                        text=message.text,
                        metadata={"export_file": key, "line": message.line_number, "media_omitted": media_omitted},
                    )
                )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        warnings = tuple(f"export removed: {name}" for name in previous if name not in current)
        return SyncBatch(events=tuple(events), checkpoint={"files": current, "scanned_at": now}, warnings=warnings)

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._batch(connection, checkpoint)

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._batch(connection, checkpoint)

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        path = Path(str(connection.settings.get("path", "")))
        return HealthReport(state="healthy" if path.exists() else "failed", error=None if path.exists() else "export path unavailable")
