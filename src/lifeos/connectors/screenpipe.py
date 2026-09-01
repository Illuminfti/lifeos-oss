from __future__ import annotations

from urllib.parse import urlparse

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import to_iso
from lifeos.contracts import CaptureActor, CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import ConfigurationError


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.screenpipe",
            display_name="Screenpipe",
            source_classes=["ocr", "accessibility", "audio_transcript", "input", "screen_metadata"],
            capabilities=["backfill", "incremental_sync", "revoke", "purge"],
            auth_modes=["localhost_api"],
            notes="First-party LifeOS integration with Screenpipe's API. It does not record, bundle, fork, or read Screenpipe's database.",
        )

    def _config(self, request):
        config = self._public_config(request)
        base = str(config.get("base_url") or "http://127.0.0.1:3030").rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"}:
            raise ConfigurationError("Screenpipe base_url must use http or https")
        if not config.get("allow_remote") and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigurationError("remote Screenpipe endpoints require allow_remote=true")
        classes = config.get("content_types") or ["accessibility", "ocr", "audio", "input"]
        if any(value in classes for value in ["video", "frames", "audio_files"]) and not config.get("copy_raw_media"):
            raise ConfigurationError("raw Screenpipe media requires copy_raw_media=true")
        return config, base, [str(value) for value in classes]

    def _headers(self, request):
        reference = request.get("secret_ref")
        if not reference:
            return {}
        token = self.context.secrets.resolve_text(reference)
        return {"Authorization": f"Bearer {token}"}

    def connect(self, request):
        try:
            config, base, classes = self._config(request)
            health = self.context.http.request("GET", f"{base}/health", headers=self._headers(request)).json()
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "screenpipe"),
                state="healthy",
                public_config={**config, "base_url": base, "content_types": classes},
                provider_identity={"endpoint": base, "version": health.get("version")},
            )
        except Exception as exc:
            return ConnectionReceipt(ok=False, state="auth_required", error="screenpipe_unavailable", message=str(exc))

    def backfill(self, request):
        return self._search(request, False)

    def sync(self, request):
        return self._search(request, True)

    def _search(self, request, incremental):
        config, base, classes = self._config(request)
        checkpoint = request.get("checkpoint") or {}
        connection = self._connection_id(request, "screenpipe")
        last = checkpoint.get("observed_at")
        events = []
        page = 0
        max_pages = int(config.get("max_pages", 20))
        latest = last
        for _ in range(max_pages):
            page_size = min(int(config.get("page_size", 100)), 1000)
            params = {
                "limit": page_size,
                "offset": page * page_size,
                "content_type": classes,
                "start_time": last if incremental else config.get("start_time"),
                "end_time": config.get("end_time"),
            }
            payload = self.context.http.request("GET", f"{base}/search", headers=self._headers(request), params=params).json()
            items = payload.get("data") or payload.get("items") or payload.get("results") or []
            if not isinstance(items, list):
                raise ConfigurationError("Screenpipe /search response did not contain a list")
            for item in items:
                content = item.get("content") if isinstance(item.get("content"), dict) else item
                kind = str(item.get("type") or content.get("content_type") or content.get("type") or "screen")
                record = str(item.get("id") or content.get("id") or f"{kind}:{content.get('timestamp')}:{len(events)}")
                stamp = to_iso(content.get("timestamp") or content.get("created_at") or item.get("timestamp"))
                latest = max(str(latest or ""), stamp)
                text = str(content.get("text") or content.get("transcription") or content.get("ocr_text") or "")
                application = content.get("app_name") or content.get("app")
                window = content.get("window_name") or content.get("window")
                speaker = content.get("speaker")
                events.append(
                    CaptureEvent.build(
                        connector_id=self.manifest.id,
                        connection_id=connection,
                        source_record_id=record,
                        source_revision=str(content.get("updated_at") or stamp),
                        source_thread_id=f"{application}:{window}" if application or window else None,
                        kind=f"screenpipe.{kind}",
                        occurred_at=stamp,
                        text=text,
                        actors=[CaptureActor(display_name=str(speaker), provider_ref=f"screenpipe:{speaker}", role="speaker")] if speaker else [],
                        metadata={
                            "app": application, "window": window, "content_type": kind,
                            "source_ref": content.get("file_path") or content.get("frame_id"), "raw_media_copied": False,
                        },
                    )
                )
            if len(items) < page_size:
                return SyncBatch(events=events, checkpoint={"observed_at": latest}, complete=True)
            page += 1
        return SyncBatch(
            events=events,
            checkpoint={"observed_at": latest},
            complete=False,
            warnings=["Screenpipe import stopped at configured max_pages"],
        )

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            _, base, classes = self._config(request)
            payload = self.context.http.request("GET", f"{base}/health", headers=self._headers(request)).json()
            return HealthReport(
                state="healthy",
                details={"endpoint": base, "content_types": classes, "provider": payload},
                checkpoint=request.get("checkpoint") or {},
            )
        except Exception as exc:
            return HealthReport(state="failed", error=str(exc))
