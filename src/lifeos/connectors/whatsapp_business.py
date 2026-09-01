"""WhatsApp Business Platform webhook capture connector."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from typing import Any, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch, content_digest
from lifeos.errors import ConfigurationError


class WhatsAppBusinessConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.whatsapp-business",
        display_name="WhatsApp Business Platform",
        source_classes=("message", "person", "status"),
        capabilities=("webhooks", "incremental_sync", "revoke", "purge"),
        auth_modes=("webhook_signature",),
        custody="local",
        implementation_status="experimental",
        notes="Captures new WABA webhook events. It does not claim personal WhatsApp history backfill.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        secret = request.get("secret")
        if not isinstance(secret, Mapping) or not secret.get("verify_token"):
            raise ConfigurationError("WhatsApp Business requires verify_token in secret JSON")
        if not secret.get("app_secret") and not bool(request.get("allow_unsigned", False)):
            raise ConfigurationError("WhatsApp Business requires app_secret unless allow_unsigned=true is explicit")
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={
                "phone_number_id": str(request.get("phone_number_id", "")),
                "allow_unsigned": bool(request.get("allow_unsigned", False)),
            },
            granted_scopes=("whatsapp:webhook:receive",),
            secret_payload=dict(secret),
        )

    def verify_webhook(self, connection: Connection, headers: Mapping[str, str], body: bytes, context: ConnectorContext) -> bool:
        secret = context.secret_for(connection)
        app_secret = secret.get("app_secret")
        if not app_secret:
            return bool(connection.settings.get("allow_unsigned", False))
        signature = headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            return False
        expected = hmac.new(str(app_secret).encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature.removeprefix("sha256="), expected)

    def webhook_challenge(self, connection: Connection, query: Mapping[str, str], context: ConnectorContext) -> str | None:
        secret = context.secret_for(connection)
        if query.get("hub.mode") != "subscribe":
            return None
        if not hmac.compare_digest(str(query.get("hub.verify_token", "")), str(secret.get("verify_token", ""))):
            return None
        return query.get("hub.challenge")

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return SyncBatch(events=(), checkpoint=dict(checkpoint), warnings=("WhatsApp Business Platform does not provide personal chat history backfill through this connector.",))

    @staticmethod
    def _text(message: Mapping[str, Any]) -> str:
        kind = str(message.get("type", "unknown"))
        value = message.get(kind)
        if isinstance(value, Mapping):
            if value.get("body"):
                return str(value["body"])
            if value.get("caption"):
                return str(value["caption"])
        return f"[{kind} message]"

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        previously_delivered = [int(value) for value in checkpoint.get("delivered_webhook_ids", [])]
        context.store.ack_webhooks(previously_delivered)
        pending = context.store.pending_webhooks(connection.connection_id)
        events: list[CaptureEvent] = []
        delivered: list[int] = []
        for envelope in pending:
            delivered.append(int(envelope["webhook_id"]))
            body = envelope["body"]
            for entry in body.get("entry", []) if isinstance(body, Mapping) else []:
                if not isinstance(entry, Mapping):
                    continue
                for change in entry.get("changes", []) or []:
                    value = change.get("value") if isinstance(change, Mapping) else None
                    if not isinstance(value, Mapping):
                        continue
                    contacts = {
                        str(item.get("wa_id")): str((item.get("profile") or {}).get("name") or item.get("wa_id"))
                        for item in value.get("contacts", []) or []
                        if isinstance(item, Mapping) and item.get("wa_id")
                    }
                    metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
                    phone_number_id = str(metadata.get("phone_number_id") or connection.settings.get("phone_number_id") or "unknown")
                    for message in value.get("messages", []) or []:
                        if not isinstance(message, Mapping) or not message.get("id"):
                            continue
                        sender = str(message.get("from", "unknown"))
                        stamp = str(message.get("timestamp", ""))
                        occurred = datetime.fromtimestamp(int(stamp), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if stamp.isdigit() else str(envelope["received_at"])
                        events.append(
                            CaptureEvent.create(
                                connector_id=self.manifest.id,
                                connection_id=connection.connection_id,
                                source_record_id=str(message["id"]),
                                source_revision=content_digest(message),
                                source_thread_id=f"whatsapp:{phone_number_id}:{sender}",
                                kind="message.created",
                                occurred_at=occurred,
                                actors=(Actor(provider_ref=f"whatsapp:{sender}", display_name=contacts.get(sender, sender)),),
                                text=self._text(message),
                                raw=message,
                                metadata={"phone_number_id": phone_number_id, "message_type": message.get("type")},
                            )
                        )
                    for status in value.get("statuses", []) or []:
                        if not isinstance(status, Mapping) or not status.get("id"):
                            continue
                        events.append(
                            CaptureEvent.create(
                                connector_id=self.manifest.id,
                                connection_id=connection.connection_id,
                                source_record_id=f"status:{status['id']}:{status.get('status')}",
                                source_revision=content_digest(status),
                                source_thread_id=f"whatsapp-status:{status.get('recipient_id', 'unknown')}",
                                kind="message.status",
                                occurred_at=str(envelope["received_at"]),
                                text=str(status.get("status", "unknown")),
                                raw=status,
                            )
                        )
        return SyncBatch(events=tuple(events), checkpoint={"delivered_webhook_ids": delivered, "last_received_at": pending[-1]["received_at"] if pending else checkpoint.get("last_received_at")})

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        pending = len(context.store.pending_webhooks(connection.connection_id, limit=1000))
        return HealthReport(state="healthy", details={"pending_webhooks": pending, "backfill_supported": False})
