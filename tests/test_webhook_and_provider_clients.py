from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from lifeos.connectors.base import ConnectorContext, load
from lifeos.errors import ConfigurationError
from lifeos.ingest import IngestQueue


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_whatsapp_business_signed_webhook_to_capture(brain: Path, secret_file):
    secret_ref = secret_file(
        {
            "access_token": "synthetic",
            "app_secret": "app-secret",
            "verify_token": "verify-me",
            "phone_number_id": "phone-1",
        },
        "whatsapp.json",
    )
    queue = IngestQueue(brain / ".lifeos" / "state.sqlite")
    plugin = load("whatsapp-business", ConnectorContext(brain=brain, queue=queue))
    request = {
        "secret_ref": secret_ref,
        "connection": {"connection_id": "con_wa", "connector_key": "whatsapp-business"},
        "config": {"graph_version": "v23.0"},
    }
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "phone-1"},
                                "contacts": [{"wa_id": "15550001", "profile": {"name": "Ada"}}],
                                "messages": [
                                    {
                                        "id": "wamid.1",
                                        "from": "15550001",
                                        "timestamp": "1788264000",
                                        "type": "text",
                                        "text": {"body": "Hello from WhatsApp Business"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        },
        separators=(",", ":"),
    ).encode()
    accepted = plugin.receive_webhook(
        request,
        headers={"x-hub-signature-256": signature("app-secret", body)},
        raw_body=body,
    )
    assert accepted["queued"] is True
    assert plugin.receive_webhook(
        request,
        headers={"x-hub-signature-256": signature("app-secret", body)},
        raw_body=body,
    )["queued"] is False
    batch = plugin.sync(request)
    assert len(batch.events) == 1
    assert batch.events[0].text == "Hello from WhatsApp Business"
    assert batch.events[0].actors[0].display_name == "Ada"
    assert batch.checkpoint["_ack_webhooks"]
    history = plugin.backfill(request)
    assert history.complete is False
    assert "historical" in history.warnings[0].lower()


def test_composio_get_only_backfill_and_signed_trigger(brain: Path, fake_http, secret_file):
    secret_ref = secret_file({"api_key": "synthetic", "webhook_secret": "hook-secret"}, "composio.json")
    base = "https://backend.composio.dev/api/v3/"
    fake_http.add(
        "GET",
        f"{base}records",
        {"items": [{"id": "r1", "updated_at": "2026-09-01T12:00:00Z", "text": "Captured via Composio"}]},
    )
    queue = IngestQueue(brain / ".lifeos" / "state.sqlite")
    plugin = load("composio", ConnectorContext(brain=brain, queue=queue, http=fake_http))
    request = {
        "secret_ref": secret_ref,
        "connection": {"connection_id": "con_composio", "connector_key": "composio"},
        "config": {
            "base_url": base,
            "connected_account_id": "ca_1",
            "read_endpoints": [{"name": "records", "path": "records", "kind": "document.updated"}],
        },
    }
    batch = plugin.backfill(request)
    assert len(batch.events) == 1
    assert batch.events[0].text == "Captured via Composio"
    assert all(call["method"] == "GET" for call in fake_http.calls)

    bad = dict(request)
    bad["config"] = {**request["config"], "read_endpoints": [{"path": "actions/run", "method": "POST"}]}
    with pytest.raises(ConfigurationError):
        plugin.backfill(bad)

    body = json.dumps(
        {"id": "trigger-1", "type": "message.received", "data": {"text": "Composio trigger"}},
        separators=(",", ":"),
    ).encode()
    assert plugin.receive_webhook(
        request,
        headers={"x-composio-signature": signature("hook-secret", body)},
        raw_body=body,
    )["queued"]
    synced = plugin.sync(request)
    assert synced.events[0].text == "Composio trigger"


def test_whoop_polling_and_signed_webhook(brain: Path, fake_http, secret_file):
    secret_ref = secret_file({"access_token": "synthetic", "webhook_secret": "whoop-secret"}, "whoop.json")
    endpoint = "https://api.prod.whoop.com/developer/v2/cycle"
    fake_http.add(
        "GET",
        endpoint,
        {"records": [{"id": 7, "updated_at": "2026-09-01T09:00:00Z", "score_state": "SCORED"}]},
    )
    queue = IngestQueue(brain / ".lifeos" / "state.sqlite")
    plugin = load("whoop", ConnectorContext(brain=brain, queue=queue, http=fake_http))
    request = {
        "secret_ref": secret_ref,
        "connection": {"connection_id": "con_whoop", "connector_key": "whoop"},
        "config": {"resources": ["cycles"]},
    }
    batch = plugin.backfill(request)
    assert len(batch.events) == 1
    assert batch.events[0].kind == "whoop.cycle.updated"

    body = json.dumps(
        {"id": "hook-1", "type": "recovery.updated", "data": {"id": 9, "updated_at": "2026-09-01T10:00:00Z"}},
        separators=(",", ":"),
    ).encode()
    assert plugin.receive_webhook(
        request,
        headers={"x-whoop-signature": signature("whoop-secret", body)},
        raw_body=body,
    )["queued"]
    fake_http.add("GET", endpoint, {"records": []})
    synced = plugin.sync(request)
    assert any(event.source_record_id.startswith("recovery_updated:") for event in synced.events)


def test_x_reads_posts_without_write_endpoint(brain: Path, fake_http, secret_file):
    secret_ref = secret_file({"access_token": "synthetic-x-token"}, "x.json")
    base = "https://api.x.com/2"
    fake_http.add("GET", f"{base}/users/me", {"data": {"id": "42", "name": "Owner", "username": "owner"}})
    fake_http.add(
        "GET",
        f"{base}/users/42/tweets",
        {
            "data": [
                {
                    "id": "100",
                    "text": "Synthetic post",
                    "author_id": "42",
                    "conversation_id": "100",
                    "created_at": "2026-09-01T12:00:00Z",
                }
            ],
            "includes": {"users": [{"id": "42", "name": "Owner", "username": "owner"}]},
            "meta": {},
        },
    )
    plugin = load("x", ConnectorContext(brain=brain, http=fake_http))
    batch = plugin.backfill(
        {
            "secret_ref": secret_ref,
            "connection": {"connection_id": "con_x"},
            "config": {"include_posts": True, "include_mentions": False, "include_direct_messages": False},
        }
    )
    assert len(batch.events) == 1
    assert batch.events[0].text == "Synthetic post"
    assert all(call["method"] == "GET" for call in fake_http.calls)
    assert not any(any(word in call["url"] for word in ["/tweets/", "/likes", "/retweets"]) for call in fake_http.calls)
