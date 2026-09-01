"""Composio trigger webhook bridge.

Composio remains one bridge into LifeOS, not the connector architecture. Configure
selected read-only Composio triggers to POST their events to this connection's
LifeOS webhook URL.
"""
from __future__ import annotations

import hmac
import secrets
from typing import Any, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch, content_digest
from lifeos.errors import ConfigurationError


class ComposioConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.composio",
        display_name="Composio trigger bridge",
        source_classes=("trigger_event", "message", "calendar_event", "document"),
        capabilities=("webhooks", "incremental_sync", "revoke", "purge"),
        auth_modes=("bearer_webhook",),
        custody="third_party",
        implementation_status="experimental",
        notes="Receives selected Composio trigger events. Capture only; action execution is intentionally absent.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        supplied = request.get("secret")
        if supplied is not None and not isinstance(supplied, Mapping):
            raise ConfigurationError("Composio secret must be a JSON object")
        secret_payload = dict(supplied or {})
        secret_payload.setdefault("ingest_token", secrets.token_urlsafe(32))
        toolkits = request.get("toolkits", [])
        if isinstance(toolkits, str):
            toolkits = [toolkits]
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={"toolkits": [str(value) for value in toolkits]},
            granted_scopes=("composio:triggers:receive",),
            secret_payload=secret_payload,
            custody="third_party",
        )

    def verify_webhook(self, connection: Connection, headers: Mapping[str, str], body: bytes, context: ConnectorContext) -> bool:
        expected = str(context.secret_for(connection).get("ingest_token", ""))
        provided = headers.get("authorization", "")
        if provided.lower().startswith("bearer "):
            provided = provided[7:]
        return bool(expected) and hmac.compare_digest(expected, provided)

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return SyncBatch(events=(), checkpoint=dict(checkpoint), warnings=("Composio trigger delivery is prospective; historical backfill must be implemented by a provider-specific connector or an explicit Composio read tool.",))

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        context.store.ack_webhooks(int(value) for value in checkpoint.get("delivered_webhook_ids", []))
        pending = context.store.pending_webhooks(connection.connection_id)
        events: list[CaptureEvent] = []
        delivered: list[int] = []
        allowed_toolkits = set(str(value).lower() for value in connection.settings.get("toolkits", []))
        for envelope in pending:
            delivered.append(int(envelope["webhook_id"]))
            body = envelope["body"]
            if not isinstance(body, Mapping):
                continue
            payload = body.get("data") if isinstance(body.get("data"), Mapping) else body.get("payload") if isinstance(body.get("payload"), Mapping) else body
            trigger = str(body.get("trigger_name") or body.get("trigger") or body.get("type") or "trigger")
            payload_toolkit = payload.get("toolkit") if isinstance(payload, Mapping) else None
            toolkit = str(body.get("toolkit") or body.get("app") or payload_toolkit or "").lower()
            if allowed_toolkits and toolkit not in allowed_toolkits:
                continue
            event_id = str(body.get("id") or body.get("event_id") or content_digest(body))
            actor_value = payload.get("actor") if isinstance(payload, Mapping) else None
            actors = ()
            if isinstance(actor_value, Mapping) and actor_value.get("id"):
                actors = (Actor(provider_ref=f"composio:{actor_value['id']}", display_name=str(actor_value.get("name") or actor_value["id"])),)
            text = ""
            if isinstance(payload, Mapping):
                for key in ("text", "body", "summary", "title", "subject"):
                    if payload.get(key):
                        text = str(payload[key])
                        break
            events.append(
                CaptureEvent.create(
                    connector_id=self.manifest.id,
                    connection_id=connection.connection_id,
                    source_record_id=event_id,
                    source_revision=content_digest(body),
                    source_thread_id=str(body.get("connection_id") or body.get("connected_account_id") or trigger),
                    kind=f"composio.{trigger}",
                    occurred_at=str(body.get("timestamp") or body.get("created_at") or envelope["received_at"]),
                    actors=actors,
                    text=text or f"Composio trigger: {trigger}",
                    raw=body,
                    metadata={"trigger": trigger, "toolkit": toolkit},
                )
            )
        return SyncBatch(events=tuple(events), checkpoint={"delivered_webhook_ids": delivered, "last_received_at": pending[-1]["received_at"] if pending else checkpoint.get("last_received_at")})

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        return HealthReport(state="healthy", details={"pending_webhooks": len(context.store.pending_webhooks(connection.connection_id, limit=1000)), "custody": "third_party"})
