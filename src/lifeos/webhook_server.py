"""Minimal local webhook receiver for signed connector events."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from lifeos.connectors.base import ConnectorManager

MAX_BODY_BYTES = 10 * 1024 * 1024


class WebhookApplication:
    def __init__(self, brain: Path) -> None:
        self.manager = ConnectorManager(Path(brain))

    @staticmethod
    def connector_from_path(path: str) -> str:
        parsed = urlparse(path)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2 or parts[0] != "webhooks":
            raise KeyError("expected /webhooks/<connector>")
        return parts[1]

    def challenge(self, path: str) -> str:
        connector = self.connector_from_path(path)
        query = {key: value for key, value in parse_qsl(urlparse(path).query, keep_blank_values=True)}
        return str(self.manager.webhook_challenge(connector, query))

    def ingest(self, path: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        connector = self.connector_from_path(path)
        return self.manager.receive_webhook(connector, headers=headers, raw_body=body)


def make_handler(application: WebhookApplication):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LifeOSWebhook/0.2"

        def do_GET(self) -> None:  # noqa: N802
            try:
                challenge = application.challenge(self.path)
                self._send(200, challenge.encode("utf-8"), "text/plain; charset=utf-8")
            except KeyError as exc:
                self._json(404, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(403, {"ok": False, "error": type(exc).__name__, "message": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._json(411, {"ok": False, "error": "content_length_required"})
                return
            try:
                length = int(raw_length)
            except ValueError:
                self._json(400, {"ok": False, "error": "invalid_content_length"})
                return
            if length < 0 or length > MAX_BODY_BYTES:
                self._json(413, {"ok": False, "error": "payload_too_large"})
                return
            body = self.rfile.read(length)
            headers = {str(key): str(value) for key, value in self.headers.items()}
            try:
                result = application.ingest(self.path, headers, body)
                self._json(202, result)
            except KeyError as exc:
                self._json(404, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(403, {"ok": False, "error": type(exc).__name__, "message": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            # Never echo provider bodies, signatures, challenge tokens, or secret refs.
            return

        def _json(self, status: int, value: dict[str, Any]) -> None:
            self._send(
                status,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def create_server(brain: Path, host: str = "127.0.0.1", port: int = 4789) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("webhook server binds localhost by default; terminate public TLS through an explicit reverse proxy")
    application = WebhookApplication(Path(brain))
    return ThreadingHTTPServer((host, int(port)), make_handler(application))


def serve(brain: Path, host: str = "127.0.0.1", port: int = 4789) -> None:
    server = create_server(Path(brain), host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
