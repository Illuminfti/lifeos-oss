from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from typing import Iterator
import zipfile

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.contracts import CaptureActor, CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import ConfigurationError

LINE_RE = re.compile(
    r"^\[?(?P<date>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\]?\s*(?:-|–)?\s*(?P<body>.*)$"
)


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.whatsapp-export",
            display_name="WhatsApp export",
            source_classes=["message", "thread", "person", "attachment"],
            capabilities=["backfill", "incremental_sync", "deletions", "revoke", "purge"],
            auth_modes=["local_export"],
            notes="Imports owner-supplied WhatsApp .txt or .zip exports. No account scraping.",
        )

    def _path(self, request):
        config = self._public_config(request)
        raw = request.get("export_path") or request.get("path") or config.get("path")
        if not raw:
            raise ConfigurationError("config.path is required")
        path = Path(str(raw)).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {".txt", ".zip"}:
            raise ConfigurationError("WhatsApp export must be a readable .txt or .zip")
        return path

    def connect(self, request):
        try:
            path = self._path(request)
        except Exception as exc:
            return ConnectionReceipt(ok=False, state="auth_required", error="configuration_required", message=str(exc))
        return ConnectionReceipt(
            ok=True,
            connection_id=self._connection_id(request, "waexport"),
            state="healthy",
            public_config={"path": str(path)},
            provider_identity={"file": path.name},
        )

    def _texts(self, path: Path) -> Iterator[tuple[str, str]]:
        if path.suffix.lower() == ".txt":
            yield path.name, path.read_text(encoding="utf-8-sig", errors="replace")
            return
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().endswith(".txt") and not name.startswith("__MACOSX/"):
                    yield name, archive.read(name).decode("utf-8-sig", errors="replace")

    def _messages(self, text: str) -> Iterator[tuple[str, str, str]]:
        current = None
        for line in text.splitlines():
            match = LINE_RE.match(line)
            if match:
                if current:
                    yield current
                body = match.group("body")
                sender = ""
                if ": " in body:
                    sender, body = body.split(": ", 1)
                current = (match.group("date") + " " + match.group("time"), sender.strip(), body)
            elif current:
                current = (current[0], current[1], current[2] + "\n" + line)
        if current:
            yield current

    @staticmethod
    def _parse_time(value: str) -> str:
        for form in [
            "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%m/%d/%Y %I:%M %p",
            "%m/%d/%y %I:%M %p", "%Y-%m-%d %H:%M",
        ]:
            try:
                return datetime.strptime(value, form).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                pass
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def backfill(self, request):
        return self._import(request)

    def sync(self, request):
        return self._import(request, incremental=True)

    def _import(self, request, incremental=False):
        path = self._path(request)
        connection = self._connection_id(request, "waexport")
        old = set((request.get("checkpoint") or {}).get("ids") or [])
        ids = []
        events = []
        for entry, text in self._texts(path):
            thread = Path(entry).stem
            for index, (stamp, sender, body) in enumerate(self._messages(text)):
                stable = sha256(f"{entry}\0{stamp}\0{sender}\0{body}".encode()).hexdigest()
                ids.append(stable)
                if incremental and stable in old:
                    continue
                events.append(
                    CaptureEvent.build(
                        connector_id=self.manifest.id,
                        connection_id=connection,
                        source_record_id=stable,
                        source_revision=stable,
                        source_thread_id=thread,
                        kind="message.created",
                        occurred_at=self._parse_time(stamp),
                        text=body,
                        actors=[CaptureActor(display_name=sender, provider_ref=f"export:{sender}", role="sender")] if sender else [],
                        metadata={"export_entry": entry, "thread_title": thread, "message_index": index},
                    )
                )
        return SyncBatch(
            events=events,
            checkpoint={"ids": ids, "file_hash": sha256(path.read_bytes()).hexdigest()},
            complete=True,
        )

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            path = self._path(request)
            return HealthReport(state="healthy", details={"file": path.name})
        except Exception as exc:
            return HealthReport(state="failed", error=str(exc))
