from __future__ import annotations

from hashlib import sha256
import json

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import to_iso, verify_hex_hmac
from lifeos.contracts import AttachmentRef, CaptureActor, CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import AuthenticationRequired, AuthorizationDenied, ConfigurationError


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.whatsapp-business",
            display_name="WhatsApp Business",
            source_classes=["message", "thread", "person", "business_account", "attachment"],
            capabilities=["incremental_sync", "webhooks", "attachments", "deletions", "revoke", "purge"],
            auth_modes=["oauth2", "system_user_token"],
            notes="WhatsApp Business Platform webhook capture. It does not claim personal WhatsApp history or outbound messaging.",
        )

    def _secret(self, request):
        secret = self._secret_json(request)
        for key in ["access_token", "app_secret", "verify_token", "phone_number_id"]:
            if not secret.get(key):
                raise AuthenticationRequired(f"WhatsApp Business secret requires {key}")
        return secret

    def _base(self, request, secret):
        config = self._public_config(request)
        version = str(config.get("graph_version") or secret.get("graph_version") or "v23.0")
        if not version.startswith("v"):
            raise ConfigurationError("graph_version must be explicit, for example v23.0")
        return f"https://graph.facebook.com/{version}", config

    def connect(self, request):
        try:
            secret = self._secret(request)
            base, config = self._base(request, secret)
            data = self.context.http.request(
                "GET",
                f"{base}/{secret['phone_number_id']}",
                headers={"Authorization": f"Bearer {secret['access_token']}"},
                params={"fields": "id,display_phone_number,verified_name"},
            ).json()
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "wabiz"),
                state="healthy",
                custody="third_party",
                scopes=["whatsapp_business_messaging", "whatsapp_business_management"],
                public_config={**config, "graph_version": base.rsplit("/", 1)[-1]},
                provider_identity={
                    "phone_number_id": data.get("id"),
                    "display_phone_number": data.get("display_phone_number"),
                    "verified_name": data.get("verified_name"),
                },
            )
        except Exception as exc:
            return self._auth_failure(exc)

    def backfill(self, request):
        return SyncBatch(
            events=[],
            checkpoint={},
            complete=False,
            warnings=["WhatsApp Business Platform does not provide a general historical inbox export; webhook capture begins after connection"],
        )

    def verify_webhook_challenge(self, request, query):
        secret = self._secret(request)
        if query.get("hub.mode") != "subscribe" or query.get("hub.verify_token") != secret["verify_token"]:
            raise AuthorizationDenied("WhatsApp webhook challenge rejected")
        return str(query.get("hub.challenge") or "")

    def receive_webhook(self, request, *, headers, raw_body):
        secret = self._secret(request)
        signature = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256") or ""
        if not verify_hex_hmac(str(secret["app_secret"]), raw_body, signature, "sha256="):
            raise AuthorizationDenied("invalid WhatsApp webhook signature")
        payload = json.loads(raw_body.decode("utf-8"))
        message_ids = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                message_ids.extend(str(message.get("id")) for message in value.get("messages") or [] if message.get("id"))
        provider_event_id = message_ids[0] if len(message_ids) == 1 else sha256(raw_body).hexdigest()
        key = str((request.get("connection") or {}).get("connector_key") or "whatsapp-business")
        queued = self.context.queue.enqueue_webhook(
            key, provider_event_id, {str(name): str(value) for name, value in headers.items()}, raw_body
        )
        return {"ok": True, "queued": queued, "provider_event_id": provider_event_id}

    def sync(self, request):
        key = str((request.get("connection") or {}).get("connector_key") or "whatsapp-business")
        connection = self._connection_id(request, "wabiz")
        events = []
        acknowledgements = []
        for envelope in self.context.queue.pending_webhooks(key, int(self._public_config(request).get("webhook_batch", 100))):
            payload = json.loads(envelope["raw_body"].decode("utf-8"))
            acknowledgements.append(envelope["webhook_id"])
            for entry in payload.get("entry") or []:
                for change in entry.get("changes") or []:
                    value = change.get("value") or {}
                    contacts = {
                        str(contact.get("wa_id")): ((contact.get("profile") or {}).get("name") or contact.get("wa_id"))
                        for contact in value.get("contacts") or []
                    }
                    for message in value.get("messages") or []:
                        message_id = str(message.get("id") or "")
                        sender = str(message.get("from") or "")
                        message_type = str(message.get("type") or "unknown")
                        body = (
                            (message.get("text") or {}).get("body")
                            or (message.get("button") or {}).get("text")
                            or (message.get("interactive") or {}).get("button_reply", {}).get("title")
                            or ""
                        )
                        media = message.get(message_type) if isinstance(message.get(message_type), dict) else {}
                        attachments = []
                        if media and media.get("id"):
                            attachments = [
                                AttachmentRef(
                                    blob_ref=f"whatsapp-business:{media['id']}",
                                    mime_type=media.get("mime_type"),
                                    name=media.get("filename"),
                                )
                            ]
                        events.append(
                            CaptureEvent.build(
                                connector_id=self.manifest.id,
                                connection_id=connection,
                                source_record_id=message_id,
                                source_revision=str(message.get("timestamp") or ""),
                                source_thread_id=sender,
                                kind="message.created",
                                occurred_at=to_iso(message.get("timestamp")),
                                text=str(body),
                                actors=[CaptureActor(display_name=str(contacts.get(sender) or sender or "Unknown sender"), provider_ref=sender or None, role="sender")],
                                attachments=attachments,
                                metadata={
                                    "message_type": message_type,
                                    "phone_number_id": (value.get("metadata") or {}).get("phone_number_id"),
                                    "context": message.get("context"),
                                },
                            )
                        )
        return SyncBatch(events=events, checkpoint={"_ack_webhooks": acknowledgements}, complete=True)

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            secret = self._secret(request)
            base, _ = self._base(request, secret)
            data = self.context.http.request(
                "GET",
                f"{base}/{secret['phone_number_id']}",
                headers={"Authorization": f"Bearer {secret['access_token']}"},
                params={"fields": "id,display_phone_number"},
            ).json()
            return HealthReport(state="healthy", details={"phone_number_id": data.get("id")})
        except Exception as exc:
            return HealthReport(state="auth_required", error=str(exc))
