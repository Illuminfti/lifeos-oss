"""Provider-neutral local webhook receiver."""
from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlparse

from lifeos.connectors.base import ConnectorContext, ConnectorRegistry

MAX_BODY = 10 * 1024 * 1024


class WebhookApplication:
    def __init__(self, registry: ConnectorRegistry, context: ConnectorContext):
        self.registry = registry
        self.context = context

    def connection(self, connection_id: str):
        found = self.context.store.get_connection(connection_id)
        if not found:
            return None
        name, connection = found
        return self.registry.get(name), connection

    def challenge(self, connection_id: str, query: Mapping[str, str]) -> str | None:
        found = self.connection(connection_id)
        if not found:
            return None
        connector, connection = found
        return connector.webhook_challenge(connection, query, self.context)

    def receive(self, connection_id: str, headers: Mapping[str, str], body: bytes) -> int:
        found = self.connection(connection_id)
        if not found:
            raise KeyError(connection_id)
        connector, connection = found
        if "webhooks" not in connector.manifest.capabilities:
            raise PermissionError("connector does not accept webhooks")
        if not connector.verify_webhook(connection, headers, body, self.context):
            raise PermissionError("webhook authentication failed")
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("webhook body must be a JSON object")
        return self.context.store.add_webhook(connection_id, headers=headers, body=parsed)


def handler_factory(application: WebhookApplication):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LifeOSWebhook/0.2"

        def log_message(self, format: str, *args: object) -> None:
            # Never log headers or request bodies. The path contains only an opaque id.
            print(f"webhook {self.address_string()} {format % args}")

        def _connection_id(self) -> str | None:
            path = urlparse(self.path).path.rstrip("/")
            prefix = "/v1/webhooks/"
            if not path.startswith(prefix):
                return None
            value = path.removeprefix(prefix)
            return value if value and "/" not in value else None

        def _reply(self, status: HTTPStatus, payload: Mapping[str, Any] | str) -> None:
            if isinstance(payload, str):
                body = payload.encode()
                content_type = "text/plain; charset=utf-8"
            else:
                body = json.dumps(payload).encode()
                content_type = "application/json"
            self.send_response(status.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            connection_id = self._connection_id()
            if not connection_id:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            query = dict(parse_qsl(urlparse(self.path).query, keep_blank_values=True))
            challenge = application.challenge(connection_id, query)
            if challenge is None:
                self._reply(HTTPStatus.FORBIDDEN, {"error": "challenge_denied"})
                return
            self._reply(HTTPStatus.OK, challenge)

        def do_POST(self) -> None:
            connection_id = self._connection_id()
            if not connection_id:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
                return
            if length <= 0 or length > MAX_BODY:
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_size"})
                return
            body = self.rfile.read(length)
            headers = {key.lower(): value for key, value in self.headers.items()}
            try:
                webhook_id = application.receive(connection_id, headers, body)
            except KeyError:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown_connection"})
            except PermissionError:
                self._reply(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            except (ValueError, json.JSONDecodeError):
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            else:
                self._reply(HTTPStatus.ACCEPTED, {"accepted": True, "webhook_id": webhook_id})

    return Handler


def serve_webhooks(registry: ConnectorRegistry, context: ConnectorContext, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    app = WebhookApplication(registry, context)
    server = ThreadingHTTPServer((host, port), handler_factory(app))
    try:
        server.serve_forever()
    finally:
        server.server_close()
