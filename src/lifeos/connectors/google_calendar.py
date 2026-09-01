from __future__ import annotations

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import to_iso
from lifeos.contracts import CaptureActor, CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import FullResyncRequired
from lifeos.oauth import OAuthTokenProvider

API = "https://www.googleapis.com/calendar/v3"


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.google-calendar",
            display_name="Google Calendar",
            source_classes=["calendar", "event", "person"],
            capabilities=["backfill", "incremental_sync", "deletions", "revoke", "purge"],
            auth_modes=["oauth2"],
            notes="Read-only Google Calendar API client.",
            config_schema={"scopes": ["https://www.googleapis.com/auth/calendar.readonly"]},
        )

    def _auth(self, request):
        secret = self._secret_json(request)
        return {"Authorization": f"Bearer {OAuthTokenProvider(self.context.http).access_token(secret)}"}

    def connect(self, request):
        try:
            headers = self._auth(request)
            calendars = self.context.http.request(
                "GET", f"{API}/users/me/calendarList", headers=headers, params={"maxResults": 1}
            ).json()
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "gcal"),
                state="healthy",
                scopes=["calendar.readonly"],
                public_config=self._public_config(request),
                provider_identity={"calendar_count_visible": len(calendars.get("items") or [])},
            )
        except Exception as exc:
            return self._auth_failure(exc)

    def _calendars(self, headers, config):
        explicit = config.get("calendar_ids")
        if explicit:
            return [str(value) for value in explicit]
        ids = []
        page = None
        for _ in range(20):
            data = self.context.http.request(
                "GET", f"{API}/users/me/calendarList", headers=headers, params={"maxResults": 250, "pageToken": page}
            ).json()
            ids.extend(str(item["id"]) for item in data.get("items") or [] if item.get("id"))
            page = data.get("nextPageToken")
            if not page:
                break
        return ids

    def _event(self, item, calendar_id, connection):
        start = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date") or item.get("created")
        actors = []
        organizer = item.get("organizer") or {}
        if organizer.get("email") or organizer.get("displayName"):
            actors.append(
                CaptureActor(
                    display_name=organizer.get("displayName") or organizer.get("email"),
                    provider_ref=organizer.get("email"),
                    role="organizer",
                )
            )
        for attendee in item.get("attendees") or []:
            actors.append(
                CaptureActor(
                    display_name=attendee.get("displayName") or attendee.get("email") or "Unknown attendee",
                    provider_ref=attendee.get("email"),
                    role="attendee",
                )
            )
        deleted = item.get("status") == "cancelled"
        text = "\n\n".join(
            value for value in [item.get("summary") or "", item.get("description") or "", item.get("location") or ""] if value
        )
        return CaptureEvent.build(
            connector_id=self.manifest.id,
            connection_id=connection,
            source_record_id=f"{calendar_id}:{item['id']}",
            source_revision=str(item.get("etag") or item.get("updated") or ""),
            source_thread_id=str(item.get("recurringEventId") or item["id"]),
            kind="event.deleted" if deleted else "event.updated",
            occurred_at=to_iso(start),
            text=text,
            actors=actors,
            deleted=deleted,
            metadata={
                "calendar_id": calendar_id, "status": item.get("status"), "start": item.get("start"),
                "end": item.get("end"), "html_link": item.get("htmlLink"), "conference_data": item.get("conferenceData"),
            },
        )

    def backfill(self, request):
        return self._read(request, False)

    def sync(self, request):
        return self._read(request, True)

    def _read(self, request, incremental):
        headers = self._auth(request)
        config = self._public_config(request)
        connection = self._connection_id(request, "gcal")
        old = (request.get("checkpoint") or {}).get("sync_tokens") or {}
        new = {}
        events = []
        warnings = []
        for calendar_id in self._calendars(headers, config):
            page = None
            sync_token = old.get(calendar_id) if incremental else None
            if incremental and not sync_token:
                warnings.append(f"calendar {calendar_id} has no sync token; full calendar scan used")
            for _ in range(int(config.get("max_pages", 50))):
                params = {
                    "maxResults": min(int(config.get("page_size", 250)), 2500),
                    "pageToken": page,
                    "singleEvents": True,
                    "showDeleted": True,
                }
                if sync_token:
                    params["syncToken"] = sync_token
                else:
                    if config.get("time_min"):
                        params["timeMin"] = config["time_min"]
                    if config.get("time_max"):
                        params["timeMax"] = config["time_max"]
                response = self.context.http.request(
                    "GET", f"{API}/calendars/{calendar_id}/events", headers=headers, params=params, allowed_statuses={410}
                )
                if response.status == 410:
                    raise FullResyncRequired(f"Google Calendar sync token expired for {calendar_id}")
                data = response.json()
                events.extend(self._event(item, calendar_id, connection) for item in data.get("items") or [] if item.get("id"))
                page = data.get("nextPageToken")
                if not page:
                    new[calendar_id] = str(data.get("nextSyncToken") or sync_token or "")
                    break
            if page:
                warnings.append(f"calendar {calendar_id} stopped at max_pages")
        return SyncBatch(events=events, checkpoint={"sync_tokens": new}, complete=not warnings, warnings=warnings)

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            self.context.http.request(
                "GET", f"{API}/users/me/calendarList", headers=self._auth(request), params={"maxResults": 1}
            )
            return HealthReport(state="healthy", checkpoint=request.get("checkpoint") or {})
        except Exception as exc:
            return HealthReport(state="auth_required", error=str(exc))
