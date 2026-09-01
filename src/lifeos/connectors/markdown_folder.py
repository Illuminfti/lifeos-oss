"""First-party Markdown folder connector."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import ConfigurationError


def _stamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


class MarkdownFolderConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.markdown-folder",
        display_name="Markdown folder",
        source_classes=("document", "note"),
        capabilities=("backfill", "incremental_sync", "deletions", "revoke", "purge"),
        auth_modes=("filesystem_consent",),
        custody="local",
        implementation_status="working",
        notes="Reads only the explicitly selected folder. Never edits source files.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        raw = request.get("path")
        if not raw:
            raise ConfigurationError("markdown-folder requires `path`")
        source = Path(str(raw)).expanduser().resolve()
        if not source.is_dir():
            raise ConfigurationError(f"not a directory: {source}")
        brain = context.config.root.resolve()
        if source == brain or brain in source.parents or source in brain.parents:
            raise ConfigurationError("source folder must not contain or live inside the LifeOS brain")
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={
                "path": str(source),
                "glob": str(request.get("glob", "**/*.md")),
                "max_bytes": int(request.get("max_bytes", 2_000_000)),
            },
            granted_scopes=("filesystem:read",),
        )

    def _scan(self, connection: Connection) -> tuple[dict[str, str], dict[str, tuple[bytes, str]]]:
        root = Path(str(connection.settings["path"]))
        pattern = str(connection.settings.get("glob", "**/*.md"))
        max_bytes = int(connection.settings.get("max_bytes", 2_000_000))
        hashes: dict[str, str] = {}
        content: dict[str, tuple[bytes, str]] = {}
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            if path.stat().st_size > max_bytes:
                continue
            data = path.read_bytes()
            relative = path.relative_to(root).as_posix()
            digest = _digest(data)
            hashes[relative] = digest
            content[relative] = (data, _stamp(path))
        return hashes, content

    def _batch(self, connection: Connection, checkpoint: Mapping[str, Any]) -> SyncBatch:
        previous = {str(k): str(v) for k, v in dict(checkpoint.get("files", {})).items()}
        current, content = self._scan(connection)
        events: list[CaptureEvent] = []
        for relative, revision in current.items():
            if previous.get(relative) == revision:
                continue
            data, occurred_at = content[relative]
            events.append(
                CaptureEvent.create(
                    connector_id=self.manifest.id,
                    connection_id=connection.connection_id,
                    source_record_id=relative,
                    source_revision=revision,
                    source_thread_id=relative,
                    kind="document.updated" if relative in previous else "document.created",
                    occurred_at=occurred_at,
                    text=data.decode("utf-8", errors="replace"),
                    metadata={"relative_path": relative, "source_root_label": Path(str(connection.settings["path"])).name},
                )
            )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        for relative, old_revision in previous.items():
            if relative in current:
                continue
            events.append(
                CaptureEvent.create(
                    connector_id=self.manifest.id,
                    connection_id=connection.connection_id,
                    source_record_id=relative,
                    source_revision=f"deleted:{old_revision}",
                    source_thread_id=relative,
                    kind="document.deleted",
                    occurred_at=now,
                    deleted=True,
                    metadata={"relative_path": relative},
                )
            )
        return SyncBatch(events=tuple(events), checkpoint={"files": current, "scanned_at": now})

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._batch(connection, checkpoint)

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._batch(connection, checkpoint)

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        source = Path(str(connection.settings.get("path", "")))
        if not source.is_dir():
            return HealthReport(state="failed", error="source folder is unavailable")
        return HealthReport(state="healthy", details={"source_label": source.name})
