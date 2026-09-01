from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

from lifeos.connectors.base import ConnectorContext
from lifeos.connectors.gmail import GmailConnector
from lifeos.connectors.google_calendar import GoogleCalendarConnector
from lifeos.connectors.imap import ImapConnector
from lifeos.connectors.telegram import TelegramConnector
from lifeos.connectors.whoop import WhoopConnector
from lifeos.connectors.x import XConnector
from lifeos.contracts import Connection
from lifeos.errors import ConfigurationError, ConnectorError
from lifeos.secrets import FileSecretStore
from lifeos.storage import StateStore


def make_context(brain):
    store = StateStore(brain.db_path)
    return store, ConnectorContext(brain, store, FileSecretStore(brain.secrets_path))


def store_connection(connector, result, ctx, name):
    secret_ref = ctx.secrets.put(result.secret_payload, label=name) if result.secret_payload else None
    connection = Connection(
        connection_id=result.connection_id,
        connector_id=connector.manifest.id,
        settings=result.settings,
        granted_scopes=result.granted_scopes,
        secret_ref=secret_ref,
    )
    ctx.store.put_connection(connection, connector_name=name)
    return connection


def test_credentialed_connectors_fail_closed_without_secret(brain):
    store, ctx = make_context(brain)
    try:
        cases = [
            (GmailConnector(), {}),
            (GoogleCalendarConnector(), {}),
            (WhoopConnector(), {}),
            (XConnector(), {"user_id": "1"}),
            (ImapConnector(), {"host": "imap.example.test"}),
            (TelegramConnector(), {}),
        ]
        for connector, request in cases:
            with pytest.raises(ConfigurationError):
                connector.connect(request, ctx)
    finally:
        store.close()


def test_gmail_backfill_normalizes_message(brain, monkeypatch):
    store, ctx = make_context(brain)
    try:
        connector = GmailConnector()
        result = connector.connect({"secret": {"access_token": "token"}}, ctx)
        connection = store_connection(connector, result, ctx, "email-gmail")
        monkeypatch.setattr("lifeos.connectors.gmail.oauth_access_token", lambda *a, **k: "token")

        def fake_request(method, url, **kwargs):
            if url.endswith("/messages"):
                return {"messages": [{"id": "m1"}]}, {}
            if url.endswith("/messages/m1"):
                return {
                    "id": "m1",
                    "threadId": "t1",
                    "historyId": "9",
                    "internalDate": "1788256800000",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "From", "value": "Ada <ada@example.test>"},
                            {"name": "To", "value": "owner@example.test"},
                            {"name": "Subject", "value": "Hello"},
                        ],
                        "body": {"data": "Qm9keQ"},
                    },
                }, {}
            if url.endswith("/profile"):
                return {"historyId": "10"}, {}
            raise AssertionError(url)

        monkeypatch.setattr("lifeos.connectors.gmail.request_json", fake_request)
        batch = connector.backfill(connection, {}, ctx)
        assert len(batch.events) == 1
        event = batch.events[0]
        assert "Subject: Hello" in event.text
        assert "Body" in event.text
        assert event.actors[0].display_name == "Ada"
        assert batch.checkpoint["history_id"] == "10"
    finally:
        store.close()


def test_google_calendar_backfill_normalizes_attendees(brain, monkeypatch):
    store, ctx = make_context(brain)
    try:
        connector = GoogleCalendarConnector()
        result = connector.connect({"secret": {"access_token": "token"}}, ctx)
        connection = store_connection(connector, result, ctx, "google-calendar")
        monkeypatch.setattr("lifeos.connectors.google_calendar.oauth_access_token", lambda *a, **k: "token")

        def fake_request(method, url, **kwargs):
            return {
                "items": [
                    {
                        "id": "event-1",
                        "etag": "v1",
                        "summary": "Planning",
                        "description": "Discuss roadmap",
                        "start": {"dateTime": "2026-09-01T10:00:00+00:00"},
                        "end": {"dateTime": "2026-09-01T11:00:00+00:00"},
                        "attendees": [
                            {"email": "ada@example.test", "displayName": "Ada"}
                        ],
                        "status": "confirmed",
                    }
                ],
                "nextSyncToken": "sync-1",
            }, {}

        monkeypatch.setattr("lifeos.connectors.google_calendar.request_json", fake_request)
        batch = connector.backfill(connection, {}, ctx)
        assert len(batch.events) == 1
        assert batch.events[0].text.startswith("Planning")
        assert batch.events[0].actors[0].provider_ref == "email:ada@example.test"
        assert batch.checkpoint["sync_tokens"]["primary"] == "sync-1"
    finally:
        store.close()


def test_whoop_backfill_is_typed_and_missing_resources_are_warnings(brain, monkeypatch):
    store, ctx = make_context(brain)
    try:
        connector = WhoopConnector()
        result = connector.connect(
            {"secret": {"access_token": "token"}, "resources": ["recovery"]}, ctx
        )
        connection = store_connection(connector, result, ctx, "whoop")
        monkeypatch.setattr("lifeos.connectors.whoop.oauth_access_token", lambda *a, **k: "token")
        monkeypatch.setattr(
            "lifeos.connectors.whoop.request_json",
            lambda *a, **k: (
                {
                    "records": [
                        {
                            "cycle_id": 7,
                            "created_at": "2026-09-01T08:00:00Z",
                            "score": {"recovery_score": 73, "resting_heart_rate": 55},
                        }
                    ]
                },
                {},
            ),
        )
        batch = connector.backfill(connection, {}, ctx)
        assert len(batch.events) == 1
        assert batch.events[0].kind == "whoop.recovery"
        assert "recovery_score=73" in batch.events[0].text
    finally:
        store.close()


def test_x_backfill_preserves_rate_and_access_boundaries(brain, monkeypatch):
    store, ctx = make_context(brain)
    try:
        connector = XConnector()
        result = connector.connect(
            {
                "secret": {"bearer_token": "token"},
                "user_id": "42",
                "streams": ["timeline"],
            },
            ctx,
        )
        connection = store_connection(connector, result, ctx, "x")

        def fake_request(method, url, **kwargs):
            if url.endswith("/users/42/tweets"):
                return {
                    "data": [
                        {
                            "id": "100",
                            "author_id": "42",
                            "created_at": "2026-09-01T10:00:00Z",
                            "conversation_id": "100",
                            "text": "A post",
                        }
                    ],
                    "includes": {
                        "users": [{"id": "42", "name": "Ada", "username": "ada"}]
                    },
                    "meta": {},
                }, {"x-rate-limit-remaining": "4"}
            if url.endswith("/users/42"):
                return {"data": {"id": "42"}}, {"x-rate-limit-remaining": "4"}
            raise AssertionError(url)

        monkeypatch.setattr("lifeos.connectors.x.request_json", fake_request)
        batch = connector.backfill(connection, {}, ctx)
        assert len(batch.events) == 1
        assert batch.events[0].actors[0].display_name == "Ada"
        assert batch.checkpoint["since_ids"]["timeline"] == "100"
        assert connector.health(connection, ctx).details["x-rate-limit-remaining"] == "4"
    finally:
        store.close()


def test_imap_backfill_uses_uid_checkpoint(brain, monkeypatch):
    raw = EmailMessage()
    raw["From"] = "Ada <ada@example.test>"
    raw["To"] = "owner@example.test"
    raw["Subject"] = "Hello"
    raw["Date"] = "Tue, 01 Sep 2026 10:00:00 +0000"
    raw["Message-ID"] = "<message-1@example.test>"
    raw.set_content("Body")
    encoded = raw.as_bytes()

    class FakeImap:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password):
            return "OK", []

        def select(self, mailbox, readonly=True):
            return "OK", [b"1"]

        def response(self, name):
            return name, [b"99"]

        def uid(self, command, *args):
            if command == "search":
                return "OK", [b"1"]
            if command == "fetch":
                return "OK", [(b"1 (BODY[])", encoded), b")"]
            raise AssertionError(command)

        def noop(self):
            return "OK", []

        def logout(self):
            return "BYE", []

    monkeypatch.setattr("lifeos.connectors.imap.imaplib.IMAP4_SSL", FakeImap)
    store, ctx = make_context(brain)
    try:
        connector = ImapConnector()
        result = connector.connect(
            {
                "host": "imap.example.test",
                "secret": {"username": "owner", "password": "password"},
            },
            ctx,
        )
        connection = store_connection(connector, result, ctx, "email-imap")
        batch = connector.backfill(connection, {}, ctx)
        assert len(batch.events) == 1
        assert batch.events[0].text.startswith("Subject: Hello")
        assert batch.checkpoint == {"uidvalidity": "99", "last_uid": 1}
    finally:
        store.close()


def test_telegram_dependency_failure_is_explicit(brain, monkeypatch):
    store, ctx = make_context(brain)
    try:
        connector = TelegramConnector()
        result = connector.connect(
            {
                "secret": {"api_id": "1", "api_hash": "hash", "phone": "+10000000000"},
                "authorize": False,
            },
            ctx,
        )
        connection = store_connection(connector, result, ctx, "telegram")
        monkeypatch.setattr(
            connector,
            "_telethon",
            lambda: (_ for _ in ()).throw(ConnectorError("install telegram extra")),
        )
        assert connector.health(connection, ctx).state == "failed"
    finally:
        store.close()
