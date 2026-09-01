from pathlib import Path

import pytest

from lifeos.contracts import CaptureEvent, ConnectionReceipt, ConnectorManifest, ContractError
from lifeos.ingest import IngestQueue


def event(revision: str = "1", text: str = "hello") -> CaptureEvent:
    return CaptureEvent.build(
        connector_id="org.lifeos.example",
        connection_id="con_test",
        source_record_id="record-1",
        source_revision=revision,
        kind="message.created",
        occurred_at="2026-09-01T12:00:00Z",
        text=text,
    )


def test_contract_rejects_outbound_capture_manifest():
    manifest = ConnectorManifest(
        id="org.lifeos.bad",
        display_name="Bad",
        source_classes=["message"],
        capabilities=["incremental_sync"],
        auth_modes=["oauth2"],
        outbound_actions=True,
    )
    with pytest.raises(ContractError):
        manifest.to_dict()


def test_capture_event_schema_and_stable_duplicate(tmp_path: Path):
    first = event()
    second = event()
    assert first.to_dict()["schema"] == "lifeos.capture-event/v1"
    assert first.content_hash == second.content_hash

    queue = IngestQueue(tmp_path / "state.sqlite")
    assert queue.accept(first) is True
    assert queue.accept(second) is False
    assert queue.accept(event("2", "edited")) is True
    assert queue.count() == 2


def test_ingest_claim_ack_retry_and_dead_letter(tmp_path: Path):
    queue = IngestQueue(tmp_path / "state.sqlite")
    capture = event()
    assert queue.accept(capture)
    claimed = queue.claim(limit=1, lease_seconds=1)
    assert [item.event_id for item in claimed] == [capture.event_id]
    queue.ack(capture.event_id)
    assert queue.count("done") == 1

    failing = CaptureEvent.build(
        connector_id="org.lifeos.example",
        connection_id="con_test",
        source_record_id="record-2",
        source_revision="1",
        kind="document.created",
        occurred_at="2026-09-01T12:01:00Z",
        text="bad downstream input",
    )
    assert queue.accept(failing)
    assert queue.fail(failing.event_id, "synthetic failure", max_attempts=1) == "dead"
    assert queue.count("dead") == 1


def test_connections_checkpoints_and_webhook_inbox(tmp_path: Path):
    queue = IngestQueue(tmp_path / "state.sqlite")
    receipt = ConnectionReceipt(
        ok=True,
        connection_id="con_1",
        state="healthy",
        scopes=["read"],
        public_config={"folder": "INBOX"},
        provider_identity={"account": "synthetic"},
    )
    queue.save_connection("email-imap", "org.lifeos.email-imap", receipt, "env:IMAP_SECRET")
    saved = queue.get_connection("email-imap")
    assert saved is not None
    assert saved["secret_ref"] == "env:IMAP_SECRET"
    assert saved["public_config"] == {"folder": "INBOX"}

    queue.set_checkpoint("email-imap", "sync", {"uid": 42})
    assert queue.get_checkpoint("email-imap", "sync") == {"uid": 42}

    assert queue.enqueue_webhook("email-imap", "provider-1", {"x-test": "1"}, b"{}") is True
    assert queue.enqueue_webhook("email-imap", "provider-1", {"x-test": "1"}, b"{}") is False
    pending = queue.pending_webhooks("email-imap")
    assert len(pending) == 1
    queue.mark_webhook_processed(pending[0]["webhook_id"])
    assert queue.pending_webhooks("email-imap") == []

    queue.revoke_connection("email-imap")
    revoked = queue.get_connection("email-imap")
    assert revoked is not None
    assert revoked["secret_ref"] is None
    assert revoked["state"] == "disconnected"
