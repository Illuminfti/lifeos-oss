"""Optional first-party Screenpipe desktop audio/video capture connector."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Mapping
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch, content_digest, ensure_iso8601
from lifeos.errors import ConfigurationError, ConnectorError


class ScreenpipeConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.screenpipe",
        display_name="Screenpipe desktop capture",
        source_classes=("screen_text", "audio_transcript", "window_activity"),
        capabilities=("backfill", "incremental_sync", "revoke", "purge"),
        auth_modes=("local_service",),
        custody="local",
        implementation_status="working",
        notes="Optional connector to Screenpipe's local API. LifeOS does not install or require its daemon.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        base_url = str(request.get("base_url", "http://127.0.0.1:3030")).rstrip("/")
        parsed = urlparse(base_url)
        allow_remote = bool(request.get("allow_remote", False))
        if parsed.scheme not in {"http", "https"}:
            raise ConfigurationError("Screenpipe base_url must be HTTP or HTTPS")
        if not allow_remote and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigurationError("remote Screenpipe requires explicit allow_remote=true")
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={
                "base_url": base_url,
                "content_type": str(request.get("content_type", "all")),
                "page_size": min(200, max(1, int(request.get("page_size", 100)))),
                "max_pages": min(1000, max(1, int(request.get("max_pages", 10)))),
            },
            granted_scopes=("desktop-capture:read",),
        )

    def _json(self, url: str) -> Any:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "lifeos/0.2"})
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ConnectorError(f"Screenpipe request failed: {type(exc).__name__}: {exc}") from exc

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        try:
            payload = self._json(str(connection.settings["base_url"]) + "/health")
        except ConnectorError as exc:
            return HealthReport(state="failed", error=str(exc))
        return HealthReport(state="healthy", details={"screenpipe": payload if isinstance(payload, dict) else {"response": payload}})

    @staticmethod
    def _items(payload: Any) -> list[Mapping[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, Mapping)]
        if isinstance(payload, Mapping):
            for key in ("data", "items", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _text(item: Mapping[str, Any]) -> str:
        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, Mapping):
            for key in ("text", "transcription", "ocr_text"):
                if content.get(key):
                    return str(content[key])
        for key in ("text", "transcription", "ocr_text"):
            if item.get(key):
                return str(item[key])
        return ""

    def _batch(self, connection: Connection, checkpoint: Mapping[str, Any], *, initial: bool) -> SyncBatch:
        base_url = str(connection.settings["base_url"])
        page_size = int(connection.settings["page_size"])
        max_pages = int(connection.settings["max_pages"])
        offset = 0
        events: list[CaptureEvent] = []
        last_timestamp = str(checkpoint.get("last_timestamp", ""))
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "content_type": connection.settings.get("content_type", "all"),
                "limit": page_size,
                "offset": offset,
            }
            if last_timestamp:
                params["start_time"] = last_timestamp
            payload = self._json(base_url + "/search?" + urlencode(params))
            items = self._items(payload)
            if not items:
                break
            for item in items:
                timestamp = str(item.get("timestamp") or item.get("created_at") or datetime.now(timezone.utc).isoformat())
                try:
                    occurred_at = ensure_iso8601(timestamp)
                except ValueError:
                    occurred_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                text = self._text(item)
                record_id = str(item.get("id") or item.get("frame_id") or content_digest(item))
                revision = str(item.get("updated_at") or item.get("version") or content_digest(item))
                content_type = str(item.get("content_type") or item.get("type") or "desktop.capture")
                speaker = item.get("speaker") or item.get("speaker_name")
                actors = (Actor(provider_ref=f"screenpipe:{speaker}", display_name=str(speaker)),) if speaker else ()
                events.append(
                    CaptureEvent.create(
                        connector_id=self.manifest.id,
                        connection_id=connection.connection_id,
                        source_record_id=record_id,
                        source_revision=revision,
                        source_thread_id=str(item.get("window_name") or item.get("app_name") or content_type),
                        kind="audio.transcript" if "audio" in content_type.lower() else "screen.ocr",
                        occurred_at=occurred_at,
                        actors=actors,
                        text=text,
                        raw=item,
                        metadata={
                            "app_name": item.get("app_name"),
                            "window_name": item.get("window_name"),
                            "content_type": content_type,
                        },
                    )
                )
                if not last_timestamp or occurred_at > last_timestamp:
                    last_timestamp = occurred_at
            if len(items) < page_size:
                break
            offset += len(items)
        return SyncBatch(
            events=tuple(events),
            checkpoint={"last_timestamp": last_timestamp, "last_offset": offset, "initial_complete": initial or bool(checkpoint.get("initial_complete"))},
            complete=True,
        )

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._batch(connection, checkpoint, initial=True)

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._batch(connection, checkpoint, initial=False)
