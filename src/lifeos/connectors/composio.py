from __future__ import annotations

from hashlib import sha256
import json
from urllib.parse import urljoin, urlparse

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import compact_json, to_iso, verify_hex_hmac
from lifeos.contracts import CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import AuthenticationRequired, AuthorizationDenied, ConfigurationError


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.composio",
            display_name="Composio",
            source_classes=["connected_account", "trigger_event", "document", "message", "calendar_event"],
            capabilities=["backfill", "incremental_sync", "webhooks", "revoke", "purge"],
            auth_modes=["api_key"],
            custody="third_party",
            notes="Read-only Composio bridge. Only explicitly configured HTTPS GET endpoints and trigger payloads are captured. Tool/action execution is forbidden.",
        )

    def _settings(self, request):
        secret = self._secret_json(request)
        if not secret.get("api_key"):
            raise AuthenticationRequired("Composio secret requires api_key")
        config = self._public_config(request)
        account = str(config.get("connected_account_id") or secret.get("connected_account_id") or "")
        if not account:
            raise ConfigurationError("connected_account_id is required")
        base = str(config.get("base_url") or "https://backend.composio.dev/api/v3/")
        if urlparse(base).scheme != "https":
            raise ConfigurationError("Composio base_url must use HTTPS")
        return secret, config, account, base if base.endswith("/") else base + "/"

    @staticmethod
    def _headers(secret):
        return {"x-api-key": str(secret["api_key"])}

    def connect(self, request):
        try:
            secret, config, account, base = self._settings(request)
            data = self.context.http.request(
                "GET", urljoin(base, f"connected_accounts/{account}"), headers=self._headers(secret)
            ).json()
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "composio"),
                state="healthy",
                custody="third_party",
                public_config=config,
                provider_identity={
                    "connected_account_id": account,
                    "status": data.get("status"),
                    "toolkit": data.get("toolkit") or data.get("appName"),
                },
            )
        except Exception as exc:
            return self._auth_failure(exc)

    def _read_endpoints(self, request):
        _, config, _, base = self._settings(request)
        for item in config.get("read_endpoints") or []:
            if not isinstance(item, dict) or not item.get("path"):
                raise ConfigurationError("each Composio read_endpoint requires path")
            if str(item.get("method") or "GET").upper() != "GET":
                raise ConfigurationError("Composio capture endpoints must use GET; actions are forbidden")
            url = urljoin(base, str(item["path"]).lstrip("/"))
            if urlparse(url).netloc != urlparse(base).netloc:
                raise ConfigurationError("Composio read_endpoint may not change host")
            yield item, url

    def backfill(self, request):
        secret, _, account, _ = self._settings(request)
        events = []
        connection = self._connection_id(request, "composio")
        warnings = []
        count = 0
        for item, url in self._read_endpoints(request):
            payload = self.context.http.request("GET", url, headers=self._headers(secret), params=item.get("params") or {}).json()
            records = payload.get(item.get("items_key") or "items") if isinstance(payload, dict) else payload
            if records is None:
                records = payload.get("data") if isinstance(payload, dict) else []
            if not isinstance(records, list):
                records = [records]
            for record in records:
                if not isinstance(record, dict):
                    record = {"value": record}
                record_id = str(record.get(item.get("id_field") or "id") or sha256(compact_json(record).encode()).hexdigest())
                stamp = to_iso(record.get(item.get("time_field") or "updated_at") or record.get("created_at"))
                text = str(record.get(item.get("text_field") or "text") or compact_json(record))
                events.append(
                    CaptureEvent.build(
                        connector_id=self.manifest.id,
                        connection_id=connection,
                        source_record_id=f"{item.get('name', 'endpoint')}:{record_id}",
                        source_revision=str(record.get("updated_at") or stamp),
                        source_thread_id=str(record.get("thread_id") or "") or None,
                        kind=str(item.get("kind") or "composio.record"),
                        occurred_at=stamp,
                        text=text,
                        metadata={"connected_account_id": account, "endpoint": item.get("name") or item["path"], "record": record},
                    )
                )
                count += 1
        if not count:
            warnings.append("no read_endpoints configured or no records returned; action execution is intentionally unavailable")
        return SyncBatch(events=events, checkpoint={"last_backfill": to_iso(None)}, complete=True, warnings=warnings)

    def verify_webhook_challenge(self, request, query):
        return str(query.get("challenge") or query.get("hub.challenge") or "")

    def receive_webhook(self, request, *, headers, raw_body):
        secret, _, account, _ = self._settings(request)
        webhook_secret = str(secret.get("webhook_secret") or "")
        if not webhook_secret:
            raise AuthenticationRequired("Composio webhook_secret is required for webhook ingestion")
        supplied = headers.get("x-composio-signature") or headers.get("X-Composio-Signature") or headers.get("x-webhook-signature") or ""
        if not verify_hex_hmac(webhook_secret, raw_body, supplied, "sha256="):
            raise AuthorizationDenied("invalid Composio webhook signature")
        payload = json.loads(raw_body.decode())
        provider_id = str(payload.get("id") or payload.get("event_id") or sha256(raw_body).hexdigest())
        key = str((request.get("connection") or {}).get("connector_key") or "composio")
        queued = self.context.queue.enqueue_webhook(
            key, provider_id, {str(name): str(value) for name, value in headers.items()}, raw_body
        )
        return {"ok": True, "queued": queued, "provider_event_id": provider_id, "connected_account_id": account}

    def sync(self, request):
        key = str((request.get("connection") or {}).get("connector_key") or "composio")
        connection = self._connection_id(request, "composio")
        events = []
        acknowledgements = []
        for envelope in self.context.queue.pending_webhooks(key, int(self._public_config(request).get("webhook_batch", 100))):
            payload = json.loads(envelope["raw_body"].decode())
            record = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            record_id = str(payload.get("id") or payload.get("event_id") or envelope["provider_event_id"])
            kind = str(payload.get("type") or payload.get("trigger_name") or "composio.trigger")
            stamp = to_iso(payload.get("timestamp") or record.get("updated_at") or record.get("created_at"))
            events.append(
                CaptureEvent.build(
                    connector_id=self.manifest.id,
                    connection_id=connection,
                    source_record_id=record_id,
                    source_revision=stamp,
                    source_thread_id=str(record.get("thread_id") or "") or None,
                    kind=kind,
                    occurred_at=stamp,
                    text=str(record.get("text") or record.get("content") or compact_json(record)),
                    metadata={"trigger": kind, "payload": record},
                )
            )
            acknowledgements.append(envelope["webhook_id"])
        return SyncBatch(events=events, checkpoint={"_ack_webhooks": acknowledgements}, complete=True)

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            secret, _, account, base = self._settings(request)
            data = self.context.http.request(
                "GET", urljoin(base, f"connected_accounts/{account}"), headers=self._headers(secret)
            ).json()
            state = "healthy" if str(data.get("status", "")).lower() not in {"failed", "inactive"} else "degraded"
            return HealthReport(state=state, details={"connected_account_id": account, "status": data.get("status")})
        except Exception as exc:
            return HealthReport(state="auth_required", error=str(exc))
