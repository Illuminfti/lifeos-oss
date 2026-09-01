from __future__ import annotations

from hashlib import sha256
import json
from urllib.parse import urljoin

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import compact_json, to_iso, verify_hex_hmac
from lifeos.contracts import CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import AuthenticationRequired, AuthorizationDenied
from lifeos.oauth import OAuthTokenProvider

DEFAULT_RESOURCES = {
    "cycles": "v2/cycle",
    "recoveries": "v2/recovery",
    "sleeps": "v2/activity/sleep",
    "workouts": "v2/activity/workout",
    "body": "v2/user/measurement/body",
}


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.whoop",
            display_name="WHOOP",
            source_classes=["profile", "cycle", "recovery", "sleep", "workout", "body_measurement"],
            capabilities=["backfill", "incremental_sync", "webhooks", "revoke", "purge"],
            auth_modes=["oauth2"],
            custody="third_party",
            notes="Read-only WHOOP developer API and signed webhook capture. No device control.",
        )

    def _settings(self, request):
        secret = self._secret_json(request)
        config = self._public_config(request)
        base = str(config.get("base_url") or "https://api.prod.whoop.com/developer/")
        token = OAuthTokenProvider(self.context.http).access_token(secret)
        return secret, config, base if base.endswith("/") else base + "/", {"Authorization": f"Bearer {token}"}

    def connect(self, request):
        try:
            _, config, base, headers = self._settings(request)
            profile = self.context.http.request("GET", urljoin(base, "v2/user/profile/basic"), headers=headers).json()
            scopes = [
                str(value)
                for value in config.get("scopes")
                or ["read:profile", "read:cycles", "read:recovery", "read:sleep", "read:workout", "read:body_measurement"]
            ]
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "whoop"),
                state="healthy",
                custody="third_party",
                scopes=scopes,
                public_config=config,
                provider_identity={
                    "user_id": profile.get("user_id") or profile.get("id"),
                    "email": profile.get("email"),
                    "first_name": profile.get("first_name"),
                    "last_name": profile.get("last_name"),
                },
            )
        except Exception as exc:
            return self._auth_failure(exc)

    def _record_event(self, name, record, connection):
        record_id = str(
            record.get("id")
            or record.get("cycle_id")
            or record.get("sleep_id")
            or record.get("workout_id")
            or sha256(compact_json(record).encode()).hexdigest()
        )
        stamp = to_iso(record.get("updated_at") or record.get("created_at") or record.get("start") or record.get("timestamp"))
        return CaptureEvent.build(
            connector_id=self.manifest.id,
            connection_id=connection,
            source_record_id=f"{name}:{record_id}",
            source_revision=str(record.get("updated_at") or record.get("score_state") or stamp),
            source_thread_id=str(record.get("cycle_id") or record_id),
            kind=f"whoop.{name.rstrip('s')}.updated",
            occurred_at=stamp,
            text=compact_json(record),
            metadata={"resource": name, "record": record},
        )

    def backfill(self, request):
        return self._poll(request, False)

    def sync(self, request):
        webhook = self._webhook_batch(request)
        polled = self._poll(request, True)
        polled.events.extend(webhook.events)
        polled.checkpoint["_ack_webhooks"] = webhook.checkpoint.get("_ack_webhooks", [])
        return polled

    def _poll(self, request, incremental):
        _, config, base, headers = self._settings(request)
        connection = self._connection_id(request, "whoop")
        old = request.get("checkpoint") or {}
        events = []
        latest = dict(old.get("latest") or {})
        warnings = []
        resources = config.get("resources") or list(DEFAULT_RESOURCES)
        for name in resources:
            path = (config.get("resource_paths") or {}).get(name) or DEFAULT_RESOURCES.get(name)
            if not path:
                warnings.append(f"unknown WHOOP resource: {name}")
                continue
            token = None
            newest = latest.get(name)
            for _ in range(int(config.get("max_pages", 20))):
                params = {"limit": min(int(config.get("page_size", 25)), 25), "nextToken": token}
                if incremental and newest:
                    params["start"] = newest
                data = self.context.http.request("GET", urljoin(base, path), headers=headers, params=params).json()
                records = data.get("records") or data.get("items") or ([] if not data else [data] if name == "body" else [])
                for record in records:
                    event = self._record_event(name, record, connection)
                    events.append(event)
                    newest = max(str(newest or ""), event.occurred_at)
                token = data.get("next_token") or data.get("nextToken")
                if not token:
                    break
            if token:
                warnings.append(f"WHOOP {name} stopped at configured max_pages")
            if newest:
                latest[name] = newest
        return SyncBatch(events=events, checkpoint={"latest": latest}, complete=not warnings, warnings=warnings)

    def verify_webhook_challenge(self, request, query):
        return str(query.get("challenge") or "")

    def receive_webhook(self, request, *, headers, raw_body):
        secret, _, _, _ = self._settings(request)
        webhook_secret = str(secret.get("webhook_secret") or "")
        if not webhook_secret:
            raise AuthenticationRequired("WHOOP webhook_secret is required for webhook capture")
        supplied = headers.get("x-whoop-signature") or headers.get("X-Whoop-Signature") or headers.get("x-signature") or ""
        if not verify_hex_hmac(webhook_secret, raw_body, supplied, "sha256="):
            raise AuthorizationDenied("invalid WHOOP webhook signature")
        payload = json.loads(raw_body.decode())
        provider_id = str(payload.get("id") or payload.get("trace_id") or sha256(raw_body).hexdigest())
        key = str((request.get("connection") or {}).get("connector_key") or "whoop")
        queued = self.context.queue.enqueue_webhook(
            key, provider_id, {str(name): str(value) for name, value in headers.items()}, raw_body
        )
        return {"ok": True, "queued": queued, "provider_event_id": provider_id}

    def _webhook_batch(self, request):
        key = str((request.get("connection") or {}).get("connector_key") or "whoop")
        connection = self._connection_id(request, "whoop")
        events = []
        acknowledgements = []
        for envelope in self.context.queue.pending_webhooks(key, int(self._public_config(request).get("webhook_batch", 100))):
            payload = json.loads(envelope["raw_body"].decode())
            record = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            name = str(payload.get("type") or payload.get("event_type") or "webhook").replace(".", "_")
            events.append(self._record_event(name, record, connection))
            acknowledgements.append(envelope["webhook_id"])
        return SyncBatch(events=events, checkpoint={"_ack_webhooks": acknowledgements})

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            _, _, base, headers = self._settings(request)
            profile = self.context.http.request("GET", urljoin(base, "v2/user/profile/basic"), headers=headers).json()
            return HealthReport(
                state="healthy",
                details={"user_id": profile.get("user_id") or profile.get("id")},
                checkpoint=request.get("checkpoint") or {},
            )
        except Exception as exc:
            return HealthReport(state="auth_required", error=str(exc))
