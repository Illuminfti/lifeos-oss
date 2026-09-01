"""WHOOP read-only physiological data connector."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.connectors.http import bearer_headers, oauth_access_token, request_json
from lifeos.contracts import CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch, content_digest, ensure_iso8601
from lifeos.errors import AuthenticationRequired, ConfigurationError, ConnectorError

BASE = "https://api.prod.whoop.com/developer/v2"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
RESOURCES = {
    "cycle": "/cycle",
    "recovery": "/recovery",
    "workout": "/activity/workout",
    "sleep": "/activity/sleep",
    "body": "/user/measurement/body",
}


class WhoopConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.whoop",
        display_name="WHOOP",
        source_classes=("cycle", "recovery", "workout", "sleep", "body_measurement"),
        capabilities=("backfill", "incremental_sync", "revoke", "purge"),
        auth_modes=("oauth2",),
        custody="local",
        implementation_status="experimental",
        notes="Read-only WHOOP Developer API adapter. Live validation requires owner OAuth credentials.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        secret = request.get("secret")
        if not isinstance(secret, Mapping) or not (secret.get("access_token") or secret.get("refresh_token")):
            raise ConfigurationError("WHOOP requires OAuth secret JSON")
        resources = request.get("resources", list(RESOURCES))
        if isinstance(resources, str):
            resources = [resources]
        unknown = set(resources) - set(RESOURCES)
        if unknown:
            raise ConfigurationError("unknown WHOOP resources: " + ", ".join(sorted(unknown)))
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={
                "resources": [str(item) for item in resources],
                "backfill_days": max(1, int(request.get("backfill_days", 90))),
                "limit": min(25, max(1, int(request.get("limit", 25)))),
                "max_pages": min(1000, max(1, int(request.get("max_pages", 100)))),
            },
            granted_scopes=tuple(str(x) for x in request.get("scopes", ["read:profile", "read:cycles", "read:recovery", "read:sleep", "read:workout", "read:body_measurement"])),
            secret_payload=dict(secret),
        )

    def _auth(self, connection: Connection, context: ConnectorContext) -> dict[str, str]:
        return bearer_headers(oauth_access_token(connection, context, token_url=TOKEN_URL))

    @staticmethod
    def _timestamp(record: Mapping[str, Any]) -> str:
        for key in ("start", "created_at", "updated_at", "end"):
            if record.get(key):
                try:
                    return ensure_iso8601(str(record[key]))
                except ValueError:
                    pass
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _event(self, connection: Connection, resource: str, record: Mapping[str, Any]) -> CaptureEvent:
        record_id = str(record.get("id") or record.get("cycle_id") or content_digest(record))
        score = record.get("score") if isinstance(record.get("score"), Mapping) else {}
        text = f"WHOOP {resource} record"
        if score:
            pairs = ", ".join(f"{key}={value}" for key, value in sorted(score.items()) if not isinstance(value, (dict, list)))
            if pairs:
                text += ": " + pairs
        return CaptureEvent.create(
            connector_id=self.manifest.id,
            connection_id=connection.connection_id,
            source_record_id=f"{resource}:{record_id}",
            source_revision=str(record.get("updated_at") or content_digest(record)),
            source_thread_id=f"whoop:{resource}",
            kind=f"whoop.{resource}",
            occurred_at=self._timestamp(record),
            text=text,
            raw=record,
            metadata={"resource": resource, "score": score},
        )

    def _collect(self, connection: Connection, context: ConnectorContext, *, start: str, end: str) -> tuple[list[CaptureEvent], list[str]]:
        events: list[CaptureEvent] = []
        warnings: list[str] = []
        for resource in connection.settings.get("resources", list(RESOURCES)):
            token: str | None = None
            for _ in range(int(connection.settings.get("max_pages", 100))):
                try:
                    payload, _ = request_json(
                        "GET",
                        BASE + RESOURCES[str(resource)],
                        headers=self._auth(connection, context),
                        params={"start": start, "end": end, "limit": connection.settings.get("limit", 25), "nextToken": token},
                    )
                except ConnectorError as exc:
                    warnings.append(f"{resource}: {exc}")
                    break
                if isinstance(payload, Mapping):
                    records = payload.get("records", [])
                    token = str(payload.get("next_token") or payload.get("nextToken") or "") or None
                elif isinstance(payload, list):
                    records = payload
                    token = None
                else:
                    records = []
                    token = None
                events.extend(self._event(connection, str(resource), record) for record in records if isinstance(record, Mapping))
                if not token:
                    break
        return events, warnings

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=int(connection.settings.get("backfill_days", 90)))
        start = start_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        end = end_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        events, warnings = self._collect(connection, context, start=start, end=end)
        return SyncBatch(events=tuple(events), checkpoint={"last_end": end}, warnings=tuple(warnings))

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        end = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        start = str(checkpoint.get("last_end") or (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z"))
        events, warnings = self._collect(connection, context, start=start, end=end)
        return SyncBatch(events=tuple(events), checkpoint={"last_end": end}, warnings=tuple(warnings))

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        try:
            profile, _ = request_json("GET", BASE + "/user/profile/basic", headers=self._auth(connection, context))
            return HealthReport(state="healthy", details={"profile_available": isinstance(profile, Mapping)})
        except AuthenticationRequired as exc:
            return HealthReport(state="auth_required", error=str(exc))
        except ConnectorError as exc:
            return HealthReport(state="failed", error=str(exc))
