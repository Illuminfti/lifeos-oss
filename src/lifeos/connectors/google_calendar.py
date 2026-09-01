"""Google Calendar read-only connector."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.connectors.http import bearer_headers, oauth_access_token, request_json
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch, content_digest, ensure_iso8601
from lifeos.errors import AuthenticationRequired, ConfigurationError, ConnectorError

BASE = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleCalendarConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.google-calendar",
        display_name="Google Calendar",
        source_classes=("calendar_event", "meeting", "person"),
        capabilities=("backfill", "incremental_sync", "deletions", "revoke", "purge"),
        auth_modes=("oauth2",),
        custody="local",
        implementation_status="experimental",
        notes="Read-only Calendar REST adapter. Live provider validation requires owner OAuth credentials.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        secret = request.get("secret")
        if not isinstance(secret, Mapping) or not (secret.get("access_token") or secret.get("refresh_token")):
            raise ConfigurationError("Google Calendar requires OAuth secret JSON")
        calendar_ids = request.get("calendar_ids", ["primary"])
        if isinstance(calendar_ids, str):
            calendar_ids = [calendar_ids]
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={
                "calendar_ids": [str(value) for value in calendar_ids],
                "time_min": request.get("time_min"),
                "time_max": request.get("time_max"),
                "page_size": min(2500, max(1, int(request.get("page_size", 250)))),
                "max_pages": min(1000, max(1, int(request.get("max_pages", 20)))),
            },
            granted_scopes=tuple(str(x) for x in request.get("scopes", ["https://www.googleapis.com/auth/calendar.readonly"])),
            secret_payload=dict(secret),
        )

    def _auth(self, connection: Connection, context: ConnectorContext) -> dict[str, str]:
        return bearer_headers(oauth_access_token(connection, context, token_url=TOKEN_URL))

    @staticmethod
    def _when(value: Mapping[str, Any]) -> str:
        raw = value.get("dateTime") or value.get("date")
        if not raw:
            return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        text = str(raw)
        if len(text) == 10:
            text += "T00:00:00+00:00"
        return ensure_iso8601(text)

    def _event(self, connection: Connection, calendar_id: str, value: Mapping[str, Any]) -> CaptureEvent:
        start = value.get("start") if isinstance(value.get("start"), Mapping) else {}
        end = value.get("end") if isinstance(value.get("end"), Mapping) else {}
        attendees: list[Actor] = []
        for attendee in value.get("attendees", []) or []:
            if not isinstance(attendee, Mapping) or not attendee.get("email"):
                continue
            email = str(attendee["email"]).lower()
            attendees.append(Actor(provider_ref=f"email:{email}", display_name=str(attendee.get("displayName") or email)))
        title = str(value.get("summary") or "Untitled event")
        description = str(value.get("description") or "")
        location = str(value.get("location") or "")
        text = f"{title}\n\n{description}".strip()
        return CaptureEvent.create(
            connector_id=self.manifest.id,
            connection_id=connection.connection_id,
            source_record_id=f"{calendar_id}:{value.get('id')}",
            source_revision=str(value.get("etag") or value.get("updated") or content_digest(value)),
            source_thread_id=str(value.get("recurringEventId") or value.get("id")),
            kind="calendar.deleted" if value.get("status") == "cancelled" else "calendar.event",
            occurred_at=self._when(start),
            actors=tuple(attendees),
            text=text,
            deleted=value.get("status") == "cancelled",
            raw=value,
            metadata={
                "calendar_id": calendar_id,
                "start": self._when(start),
                "end": self._when(end),
                "location": location,
                "status": value.get("status"),
                "html_link": value.get("htmlLink"),
            },
        )

    def _calendar_batch(
        self,
        connection: Connection,
        context: ConnectorContext,
        calendar_id: str,
        sync_token: str | None,
    ) -> tuple[list[CaptureEvent], str | None, bool]:
        page_token: str | None = None
        events: list[CaptureEvent] = []
        next_sync = sync_token
        for _ in range(int(connection.settings.get("max_pages", 20))):
            params: dict[str, Any] = {
                "maxResults": connection.settings.get("page_size", 250),
                "pageToken": page_token,
                "showDeleted": "true",
                "singleEvents": "true",
            }
            if sync_token:
                params["syncToken"] = sync_token
            else:
                params["timeMin"] = connection.settings.get("time_min")
                params["timeMax"] = connection.settings.get("time_max")
                params["orderBy"] = "startTime"
            page, _ = request_json(
                "GET",
                f"{BASE}/calendars/{quote(calendar_id, safe='')}/events",
                headers=self._auth(connection, context),
                params=params,
            )
            if not isinstance(page, Mapping):
                raise ConnectorError("Calendar events response is malformed")
            events.extend(
                self._event(connection, calendar_id, item)
                for item in page.get("items", []) or []
                if isinstance(item, Mapping) and item.get("id")
            )
            page_token = str(page.get("nextPageToken")) if page.get("nextPageToken") else None
            if page.get("nextSyncToken"):
                next_sync = str(page["nextSyncToken"])
            if not page_token:
                break
        return events, next_sync, page_token is None

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        all_events: list[CaptureEvent] = []
        tokens: dict[str, str] = {}
        complete = True
        for calendar_id in connection.settings.get("calendar_ids", ["primary"]):
            events, token, done = self._calendar_batch(connection, context, str(calendar_id), None)
            all_events.extend(events)
            if token:
                tokens[str(calendar_id)] = token
            complete = complete and done
        return SyncBatch(events=tuple(all_events), checkpoint={"sync_tokens": tokens}, complete=complete)

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        previous = {str(k): str(v) for k, v in dict(checkpoint.get("sync_tokens", {})).items()}
        if not previous:
            return self.backfill(connection, checkpoint, context)
        events: list[CaptureEvent] = []
        tokens = dict(previous)
        for calendar_id in connection.settings.get("calendar_ids", ["primary"]):
            identifier = str(calendar_id)
            token = previous.get(identifier)
            if not token:
                new_events, next_token, _ = self._calendar_batch(connection, context, identifier, None)
            else:
                new_events, next_token, _ = self._calendar_batch(connection, context, identifier, token)
            events.extend(new_events)
            if next_token:
                tokens[identifier] = next_token
        return SyncBatch(events=tuple(events), checkpoint={"sync_tokens": tokens})

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        try:
            value, _ = request_json("GET", f"{BASE}/users/me/calendarList", headers=self._auth(connection, context), params={"maxResults": 1})
            return HealthReport(state="healthy", details={"calendar_access": isinstance(value, Mapping)})
        except AuthenticationRequired as exc:
            return HealthReport(state="auth_required", error=str(exc))
        except ConnectorError as exc:
            return HealthReport(state="failed", error=str(exc))
