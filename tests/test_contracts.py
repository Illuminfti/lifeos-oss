from __future__ import annotations

import pytest

from lifeos.contracts import Actor, CaptureEvent, ConnectorManifest, HealthReport


def make_event(text: str = "hello") -> CaptureEvent:
    return CaptureEvent.create(
        connector_id="org.lifeos.test",
        connection_id="con_test",
        source_record_id="record-1",
        source_revision="1",
        kind="message.created",
        occurred_at="2026-09-01T12:00:00Z",
        observed_at="2026-09-01T12:01:00Z",
        actors=(Actor(provider_ref="test:ada", display_name="Ada"),),
        text=text,
    )


def test_capture_event_is_deterministic_and_round_trips():
    first = make_event()
    second = make_event()
    assert first.event_id == second.event_id
    assert first.content_hash == second.content_hash
    assert CaptureEvent.from_dict(first.to_dict()) == first


def test_capture_revision_changes_identity():
    first = make_event("one")
    second = make_event("two")
    assert first.event_id != second.event_id
    assert first.content_hash != second.content_hash


def test_manifest_rejects_outbound_capture_plugin():
    with pytest.raises(ValueError, match="outbound"):
        ConnectorManifest(
            id="org.lifeos.bad",
            display_name="Bad",
            source_classes=("message",),
            capabilities=("send",),
            auth_modes=("oauth",),
            outbound_actions=True,
        )


def test_health_states_are_closed():
    assert HealthReport(state="healthy").to_dict()["state"] == "healthy"
    with pytest.raises(ValueError):
        HealthReport(state="vibes")
