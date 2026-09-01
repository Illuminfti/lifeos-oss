from __future__ import annotations

import json
from pathlib import Path

from lifeos.connectors.base import ConnectorContext, ConnectorRegistry
from lifeos.connectors.markdown_folder import MarkdownFolderConnector
from lifeos.connectors.screenpipe import ScreenpipeConnector
from lifeos.connectors.whatsapp_export import WhatsAppExportConnector, parse_export
from lifeos.contracts import Connection
from lifeos.secrets import FileSecretStore
from lifeos.storage import StateStore


def context(brain):
    store = StateStore(brain.db_path)
    return store, ConnectorContext(brain, store, FileSecretStore(brain.secrets_path))


def test_dynamic_registry_discovers_all_bundled_plugins():
    registry = ConnectorRegistry.discover()
    expected = {
        "telegram",
        "whatsapp-business",
        "whatsapp-export",
        "email-gmail",
        "email-imap",
        "composio",
        "whoop",
        "x",
        "screenpipe",
        "markdown-folder",
        "google-calendar",
        "example",
        "note",
    }
    assert expected <= set(registry.names())
    for registration in registry.registrations():
        assert registration.connector.manifest.outbound_actions is False
        assert registration.connector.manifest.protocol == "lifeos.connector/v1"
        assert registration.connector.manifest.implementation_status in {
            "working",
            "experimental",
            "scaffold",
        }


def test_core_registry_contains_no_provider_name_switch():
    source = Path(__file__).parents[1] / "src/lifeos/connectors/base.py"
    text = source.read_text()
    for provider in ("telegram", "gmail", "whoop", "screenpipe", "composio"):
        assert f'"{provider}"' not in text.lower()


def test_markdown_backfill_update_and_delete(brain, tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    page = notes / "hello.md"
    page.write_text("# Hello\n")
    store, ctx = context(brain)
    try:
        connector = MarkdownFolderConnector()
        result = connector.connect({"path": str(notes)}, ctx)
        connection = Connection(
            connection_id=result.connection_id,
            connector_id=connector.manifest.id,
            settings=result.settings,
        )
        first = connector.backfill(connection, {}, ctx)
        assert len(first.events) == 1
        assert first.events[0].kind == "document.created"
        page.write_text("# Hello again\n")
        second = connector.sync(connection, first.checkpoint, ctx)
        assert len(second.events) == 1
        assert second.events[0].kind == "document.updated"
        page.unlink()
        third = connector.sync(connection, second.checkpoint, ctx)
        assert len(third.events) == 1
        assert third.events[0].deleted is True
    finally:
        store.close()


def test_whatsapp_export_parses_multiline_and_emits_events(brain, tmp_path: Path):
    export = tmp_path / "Chat.txt"
    export.write_text(
        "01/09/2026, 10:01 - Ada Example: hello\n"
        "continuation\n"
        "01/09/2026, 10:02 - Grace Example: <Media omitted>\n"
    )
    parsed = parse_export(export.read_text())
    assert len(parsed) == 2
    assert parsed[0].text == "hello\ncontinuation"
    store, ctx = context(brain)
    try:
        connector = WhatsAppExportConnector()
        result = connector.connect({"path": str(export)}, ctx)
        connection = Connection(
            connection_id=result.connection_id,
            connector_id=connector.manifest.id,
            settings=result.settings,
        )
        batch = connector.backfill(connection, {}, ctx)
        assert len(batch.events) == 2
        assert {event.actors[0].display_name for event in batch.events} == {
            "Ada Example",
            "Grace Example",
        }
        assert batch.events[1].metadata["media_omitted"] is True
    finally:
        store.close()


def test_screenpipe_uses_local_health_and_search_api(brain, monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(self.payload).encode()

    calls = []

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        if request.full_url.endswith("/health"):
            return Response({"status": "ok"})
        return Response(
            {
                "data": [
                    {
                        "id": "frame-1",
                        "content_type": "ocr",
                        "timestamp": "2026-09-01T10:00:00Z",
                        "text": "Desktop text",
                        "app_name": "Editor",
                    }
                ]
            }
        )

    monkeypatch.setattr("lifeos.connectors.screenpipe.urlopen", fake_urlopen)
    store, ctx = context(brain)
    try:
        connector = ScreenpipeConnector()
        result = connector.connect({}, ctx)
        connection = Connection(
            connection_id=result.connection_id,
            connector_id=connector.manifest.id,
            settings=result.settings,
        )
        assert connector.health(connection, ctx).state == "healthy"
        batch = connector.backfill(connection, {}, ctx)
        assert len(batch.events) == 1
        assert batch.events[0].text == "Desktop text"
        assert any("/search?" in call for call in calls)
    finally:
        store.close()
