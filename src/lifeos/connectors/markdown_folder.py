from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.contracts import CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import ConfigurationError


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.markdown-folder",
            display_name="Markdown folder",
            source_classes=["document", "file"],
            capabilities=["backfill", "incremental_sync", "deletions", "revoke", "purge"],
            auth_modes=["local_path"],
            notes="Read-only recursive Markdown importer. The selected canonical brain is rejected to prevent ingest loops.",
        )

    def _path(self, request):
        config = self._public_config(request)
        raw = request.get("path") or config.get("path")
        if not raw:
            raise ConfigurationError("config.path is required")
        path = Path(str(raw)).expanduser().resolve()
        if not path.is_dir():
            raise ConfigurationError(f"Markdown folder does not exist: {path}")
        if self.context.brain and (
            path == self.context.brain or path in self.context.brain.parents or self.context.brain in path.parents
        ):
            raise ConfigurationError("source folder may not contain or equal the LifeOS brain")
        return path

    def connect(self, request):
        try:
            path = self._path(request)
        except Exception as exc:
            return ConnectionReceipt(ok=False, state="auth_required", error="configuration_required", message=str(exc))
        return ConnectionReceipt(
            ok=True,
            connection_id=self._connection_id(request, "markdown"),
            state="healthy",
            public_config={"path": str(path)},
            provider_identity={"path": str(path)},
        )

    def backfill(self, request):
        return self._scan(request, False)

    def sync(self, request):
        return self._scan(request, True)

    def _scan(self, request, incremental):
        root = self._path(request)
        connection = self._connection_id(request, "markdown")
        old = dict((request.get("checkpoint") or {}).get("files") or {})
        current = {}
        events = []
        for path in sorted(root.rglob("*.md")):
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            raw = path.read_bytes()
            digest = sha256(raw).hexdigest()
            relative = path.relative_to(root).as_posix()
            current[relative] = digest
            if incremental and old.get(relative) == digest:
                continue
            stat = path.stat()
            events.append(
                CaptureEvent.build(
                    connector_id=self.manifest.id,
                    connection_id=connection,
                    source_record_id=relative,
                    source_revision=digest,
                    kind="document.updated" if relative in old else "document.created",
                    occurred_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                    text=raw.decode("utf-8", errors="replace"),
                    metadata={"relative_path": relative, "size": stat.st_size},
                )
            )
        if incremental:
            for relative, digest in sorted(old.items()):
                if relative not in current:
                    events.append(
                        CaptureEvent.build(
                            connector_id=self.manifest.id,
                            connection_id=connection,
                            source_record_id=relative,
                            source_revision="deleted:" + digest,
                            kind="document.deleted",
                            occurred_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            deleted=True,
                            metadata={"relative_path": relative},
                        )
                    )
        return SyncBatch(events=events, checkpoint={"files": current}, complete=True)

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            path = self._path(request)
            return HealthReport(state="healthy", details={"path": str(path)})
        except Exception as exc:
            return HealthReport(state="failed", error=str(exc))
