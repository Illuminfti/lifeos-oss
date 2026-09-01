from __future__ import annotations

import base64
from pathlib import Path

import pytest

from lifeos.connectors.base import ConnectorContext, ConnectorManager, load
from lifeos.errors import FullResyncRequired
from lifeos.http import HttpResponse

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3"


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_gmail_backfill_and_history_cursor(brain: Path, fake_http, secret_file):
    secret_ref = secret_file({"access_token": "synthetic-gmail-token"}, "gmail.json")
    fake_http.add("GET", f"{GMAIL}/profile", {"emailAddress": "owner@example.test", "historyId": "100"})
    fake_http.add("GET", f"{GMAIL}/messages", {"messages": [{"id": "m1", "threadId": "t1"}]})
    fake_http.add(
        "GET",
        f"{GMAIL}/messages/m1",
        {
            "id": "m1",
            "threadId": "t1",
            "historyId": "101",
            "internalDate": "1788264000000",
            "snippet": "hello",
            "labelIds": ["INBOX"],
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Ada <ada@example.test>"},
                    {"name": "Subject", "value": "Synthetic"},
                ],
                "body": {"data": _b64("Hello from Gmail")},
            },
        },
    )
    manager = ConnectorManager(brain, http=fake_http)
    receipt = manager.connect("email-gmail", {"secret_ref": secret_ref})
    assert receipt.ok
    result = manager.run("email-gmail", "backfill")
    assert result["stored"] == 1
    assert result["checkpoint"] == {"history_id": "101"}
    event = manager.queue.claim(limit=1)[0]
    assert event.text == "Hello from Gmail"
    assert event.actors[0].provider_ref == "ada@example.test"


def test_gmail_expired_history_fails_loudly(brain: Path, fake_http, secret_file):
    secret_ref = secret_file({"access_token": "synthetic"}, "gmail-expired.json")
    fake_http.add("GET", f"{GMAIL}/history", HttpResponse(404, {}, b"{}"))
    plugin = load("email-gmail", ConnectorContext(brain=brain, http=fake_http))
    with pytest.raises(FullResyncRequired):
        plugin.sync(
            {
                "secret_ref": secret_ref,
                "connection": {"connection_id": "con_gmail"},
                "checkpoint": {"history_id": "7"},
                "config": {},
            }
        )


def test_google_calendar_backfill_uses_sync_token(brain: Path, fake_http, secret_file):
    secret_ref = secret_file({"access_token": "synthetic-calendar-token"}, "calendar.json")
    fake_http.add("GET", f"{CALENDAR}/users/me/calendarList", {"items": [{"id": "primary"}]})
    fake_http.add("GET", f"{CALENDAR}/users/me/calendarList", {"items": [{"id": "primary"}]})
    fake_http.add(
        "GET",
        f"{CALENDAR}/calendars/primary/events",
        {
            "items": [
                {
                    "id": "event-1",
                    "etag": "rev-1",
                    "status": "confirmed",
                    "summary": "Project review",
                    "start": {"dateTime": "2026-09-02T10:00:00Z"},
                    "end": {"dateTime": "2026-09-02T10:30:00Z"},
                    "organizer": {"email": "owner@example.test", "displayName": "Owner"},
                }
            ],
            "nextSyncToken": "sync-1",
        },
    )
    manager = ConnectorManager(brain, http=fake_http)
    assert manager.connect("google-calendar", {"secret_ref": secret_ref}).ok
    result = manager.run("google-calendar", "backfill")
    assert result["stored"] == 1
    assert result["checkpoint"] == {"sync_tokens": {"primary": "sync-1"}}


def test_screenpipe_is_api_adapter_not_recorder(brain: Path, fake_http):
    fake_http.add("GET", "http://127.0.0.1:3030/health", {"status": "ok", "version": "synthetic"})
    fake_http.add(
        "GET",
        "http://127.0.0.1:3030/search",
        {
            "data": [
                {
                    "id": "ocr-1",
                    "type": "ocr",
                    "content": {
                        "timestamp": "2026-09-01T12:00:00Z",
                        "text": "Synthetic screen text",
                        "app_name": "Browser",
                        "window_name": "LifeOS",
                    },
                }
            ]
        },
    )
    manager = ConnectorManager(brain, http=fake_http)
    receipt = manager.connect("screenpipe", {"config": {"base_url": "http://127.0.0.1:3030"}})
    assert receipt.ok
    result = manager.run("screenpipe", "backfill")
    assert result["stored"] == 1
    event = manager.queue.claim(limit=1)[0]
    assert event.kind == "screenpipe.ocr"
    assert event.metadata["raw_media_copied"] is False
    assert all(call["method"] == "GET" for call in fake_http.calls)


def test_screenpipe_rejects_remote_and_raw_media_without_opt_in(brain: Path):
    plugin = load("screenpipe", ConnectorContext(brain=brain))
    remote = plugin.connect({"config": {"base_url": "https://screenpipe.example.test"}})
    assert remote.ok is False
    raw = plugin.connect({"config": {"content_types": ["frames"]}})
    assert raw.ok is False
