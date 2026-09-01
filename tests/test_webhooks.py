from __future__ import annotations

import hashlib
import hmac
import json

from lifeos.connectors.base import ConnectorRegistry
from lifeos.connectors.composio import ComposioConnector
from lifeos.connectors.whatsapp_business import WhatsAppBusinessConnector
from lifeos.runtime import LifeOSRuntime
from lifeos.webhook import WebhookApplication


def test_composio_bearer_webhook_enters_same_ingest_path(brain):
    registry = ConnectorRegistry.from_connectors({"composio": ComposioConnector()})
    with LifeOSRuntime(brain, registry=registry) as runtime:
        connection = runtime.connect(
            "composio",
            {"secret": {"ingest_token": "test-token"}, "toolkits": ["gmail"]},
        )
        app = WebhookApplication(registry, runtime.context)
        body = {
            "id": "trigger-1",
            "trigger_name": "GMAIL_NEW_MESSAGE",
            "toolkit": "gmail",
            "timestamp": "2026-09-01T10:00:00Z",
            "data": {"subject": "Hello", "text": "Body"},
        }
        webhook_id = app.receive(
            connection.connection_id,
            {"authorization": "Bearer test-token"},
            json.dumps(body).encode(),
        )
        assert webhook_id > 0
        result = runtime.run_connector(connection.connection_id, stream="sync")
        assert result.ingest.accepted == 1
        assert runtime.store.stats()["events"] == 1


def test_composio_rejects_wrong_token(brain):
    registry = ConnectorRegistry.from_connectors({"composio": ComposioConnector()})
    with LifeOSRuntime(brain, registry=registry) as runtime:
        connection = runtime.connect("composio", {"secret": {"ingest_token": "right"}})
        app = WebhookApplication(registry, runtime.context)
        try:
            app.receive(
                connection.connection_id,
                {"authorization": "Bearer wrong"},
                b'{"id":"x"}',
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("wrong webhook token was accepted")


def test_whatsapp_challenge_signature_and_message_normalization(brain):
    connector = WhatsAppBusinessConnector()
    registry = ConnectorRegistry.from_connectors({"whatsapp-business": connector})
    with LifeOSRuntime(brain, registry=registry) as runtime:
        connection = runtime.connect(
            "whatsapp-business",
            {"secret": {"verify_token": "verify", "app_secret": "app-secret"}},
        )
        app = WebhookApplication(registry, runtime.context)
        assert app.challenge(
            connection.connection_id,
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "verify",
                "hub.challenge": "challenge-value",
            },
        ) == "challenge-value"
        body = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "phone-1"},
                                "contacts": [
                                    {"wa_id": "441234", "profile": {"name": "Ada"}}
                                ],
                                "messages": [
                                    {
                                        "id": "wamid.1",
                                        "from": "441234",
                                        "timestamp": "1788256800",
                                        "type": "text",
                                        "text": {"body": "Hello from WhatsApp"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        encoded = json.dumps(body).encode()
        signature = hmac.new(b"app-secret", encoded, hashlib.sha256).hexdigest()
        app.receive(
            connection.connection_id,
            {"x-hub-signature-256": "sha256=" + signature},
            encoded,
        )
        result = runtime.run_connector(connection.connection_id, stream="sync")
        assert result.ingest.accepted == 1
        event = runtime.store.list_events(limit=1)[0]
        assert event.text == "Hello from WhatsApp"
        assert event.actors[0].display_name == "Ada"
