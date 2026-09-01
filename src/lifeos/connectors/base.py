"""Connector protocol, plugin discovery, and lifecycle manager."""
from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import shutil
from typing import Any, Protocol
from uuid import uuid4

from lifeos.connectors import BUILTIN_REGISTRY, external_entrypoints, registered_connector_ids
from lifeos.contracts import CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch, utc_now
from lifeos.errors import AuthenticationRequired, ConfigurationError, UnsupportedCapability
from lifeos.http import HttpTransport, JsonHttpClient
from lifeos.ingest import IngestQueue
from lifeos.secrets import SecretResolver


@dataclass(slots=True)
class ConnectorContext:
    brain: Path | None = None
    queue: IngestQueue | None = None
    secrets: SecretResolver | None = None
    http: HttpTransport | None = None

    def __post_init__(self) -> None:
        self.secrets = self.secrets or SecretResolver()
        self.http = self.http or JsonHttpClient()


class ConnectorPlugin(Protocol):
    manifest: ConnectorManifest

    def describe(self) -> ConnectorManifest: ...
    def connect(self, request: dict[str, Any]) -> ConnectionReceipt: ...
    def backfill(self, request: dict[str, Any]) -> SyncBatch: ...
    def sync(self, request: dict[str, Any]) -> SyncBatch: ...
    def health(self, request: dict[str, Any] | None = None) -> HealthReport: ...


class BasePlugin:
    manifest: ConnectorManifest

    def __init__(self, context: ConnectorContext | None = None):
        self.context = context or ConnectorContext()

    def describe(self) -> ConnectorManifest:
        self.manifest.validate()
        return self.manifest

    def health(self, request: dict[str, Any] | None = None) -> HealthReport:
        return HealthReport(
            state="disconnected" if not request or not request.get("connection") else "degraded",
            error=None if not request else "provider health probe unavailable",
        )

    def revoke(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "credentials_deleted": True, "evidence_untouched": True}

    def purge(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "raw_deleted": True, "canon_untouched": True}

    def receive_webhook(self, request: dict[str, Any], *, headers: dict[str, str], raw_body: bytes):
        raise UnsupportedCapability(f"{self.manifest.id} has no webhook")

    def verify_webhook_challenge(self, request: dict[str, Any], query: dict[str, str]):
        raise UnsupportedCapability(f"{self.manifest.id} has no webhook challenge")

    def test_fixture(self) -> dict[str, Any]:
        event = self._fixture_event()
        return {"ok": True, "events": 1, "event_id": event.event_id, "kind": event.kind}

    def fixture_batch(self) -> SyncBatch:
        return SyncBatch(events=[self._fixture_event()], checkpoint={"fixture": 1})

    def _fixture_event(self) -> CaptureEvent:
        return CaptureEvent.build(
            connector_id=self.manifest.id,
            connection_id="fixture",
            source_record_id="fixture-1",
            kind="fixture.created",
            occurred_at=utc_now(),
            text=f"synthetic fixture; not personal data ({self.manifest.id})",
            metadata={"synthetic": True},
        )

    def _secret_json(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.context.secrets.resolve_json(request.get("secret_ref"))  # type: ignore[union-attr]

    @staticmethod
    def _connection_id(request: dict[str, Any], prefix: str) -> str:
        return str((request.get("connection") or {}).get("connection_id") or f"con_{prefix}_{uuid4().hex[:12]}")

    @staticmethod
    def _public_config(request: dict[str, Any]) -> dict[str, Any]:
        value = request.get("config") or request.get("public_config") or {}
        if not isinstance(value, dict):
            raise ConfigurationError("connector config must be an object")
        return dict(value)

    @staticmethod
    def _auth_failure(exc: Exception) -> ConnectionReceipt:
        return ConnectionReceipt(ok=False, state="auth_required", error="auth_required", message=str(exc))


def load(key: str, context: ConnectorContext | None = None):
    if key in BUILTIN_REGISTRY:
        plugin = importlib.import_module(BUILTIN_REGISTRY[key]).Plugin(context)
    else:
        entrypoint = external_entrypoints().get(key)
        if not entrypoint:
            raise KeyError(key)
        factory = entrypoint.load()
        plugin = factory(context) if callable(factory) else factory
    if plugin.describe().outbound_actions:
        raise ConfigurationError(f"capture plugin {key} declares outbound actions")
    return plugin


def load_all(context: ConnectorContext | None = None) -> dict[str, Any]:
    return {key: load(key, context) for key in registered_connector_ids()}


class ConnectorManager:
    def __init__(self, brain: Path, *, queue=None, secrets=None, http=None):
        self.brain = Path(brain).resolve()
        self.queue = queue or IngestQueue(self.brain / ".lifeos" / "state.sqlite")
        self.context = ConnectorContext(self.brain, self.queue, secrets, http)

    def connect(self, key: str, request: dict[str, Any]) -> ConnectionReceipt:
        plugin = load(key, self.context)
        receipt = plugin.connect(dict(request))
        if receipt.ok:
            self.queue.save_connection(key, plugin.describe().id, receipt, request.get("secret_ref"))
        return receipt

    def run(self, key: str, mode: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        if mode not in {"backfill", "sync"}:
            raise ValueError(mode)
        connection = self.queue.get_connection(key)
        if not connection:
            raise AuthenticationRequired(f"connector not connected: {key}")
        plugin = load(key, self.context)
        checkpoint = self.queue.get_checkpoint(key, mode)
        if mode == "sync" and not checkpoint:
            checkpoint = self.queue.get_checkpoint(key, "backfill")
        request = {
            "connection": connection,
            "secret_ref": connection.get("secret_ref"),
            "config": connection.get("public_config") or {},
            "checkpoint": checkpoint,
        }
        request.update(overrides or {})
        try:
            batch = plugin.backfill(request) if mode == "backfill" else plugin.sync(request)
            stored, duplicates = self.queue.accept_batch(batch.events)
            next_checkpoint = dict(batch.checkpoint)
            webhook_acks = list(next_checkpoint.pop("_ack_webhooks", []))
            self.queue.set_checkpoint(key, mode, next_checkpoint)
            if mode == "backfill":
                self.queue.set_checkpoint(key, "sync", next_checkpoint)
            for webhook_id in webhook_acks:
                self.queue.mark_webhook_processed(webhook_id)
            self.queue.mark_connection_result(key, True)
            return {
                "ok": True, "stored": stored, "duplicates": duplicates, "events": len(batch.events),
                "complete": batch.complete, "warnings": batch.warnings, "checkpoint": next_checkpoint,
            }
        except Exception as exc:
            self.queue.mark_connection_result(key, False, str(exc))
            raise

    def health(self, key: str) -> HealthReport:
        connection = self.queue.get_connection(key)
        plugin = load(key, self.context)
        request = None if not connection else {
            "connection": connection,
            "secret_ref": connection.get("secret_ref"),
            "config": connection.get("public_config") or {},
            "checkpoint": self.queue.get_checkpoint(key, "sync"),
        }
        return plugin.health(request)

    def receive_webhook(self, key: str, *, headers: dict[str, str], raw_body: bytes):
        connection = self.queue.get_connection(key)
        if not connection:
            raise AuthenticationRequired(f"connector not connected: {key}")
        return load(key, self.context).receive_webhook(
            {"connection": connection, "secret_ref": connection.get("secret_ref"), "config": connection.get("public_config") or {}},
            headers=headers,
            raw_body=raw_body,
        )

    def webhook_challenge(self, key: str, query: dict[str, str]):
        connection = self.queue.get_connection(key)
        if not connection:
            raise AuthenticationRequired(f"connector not connected: {key}")
        return load(key, self.context).verify_webhook_challenge(
            {"connection": connection, "secret_ref": connection.get("secret_ref"), "config": connection.get("public_config") or {}},
            query,
        )

    def revoke(self, key: str) -> dict[str, Any]:
        connection = self.queue.get_connection(key)
        if not connection:
            return {"ok": True, "already_disconnected": True}
        result = load(key, self.context).revoke(
            {"connection": connection, "secret_ref": connection.get("secret_ref"), "config": connection.get("public_config") or {}}
        )
        self.queue.revoke_connection(key)
        return result

    def purge(self, key: str) -> dict[str, Any]:
        connection = self.queue.get_connection(key)
        plugin = load(key, self.context)
        result = plugin.purge({"connection": connection} if connection else None)
        deleted = self.queue.purge_connector_events(plugin.describe().id, connection.get("connection_id") if connection else None)
        raw_directory = self.brain / "07-raw" / key
        if raw_directory.exists():
            shutil.rmtree(raw_directory)
        self.queue.delete_connection(key)
        return {"ok": True, "events_deleted": deleted, "canon_untouched": True, "plugin": result}
