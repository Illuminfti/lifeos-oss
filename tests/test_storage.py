from __future__ import annotations

import sqlite3
from pathlib import Path

from lifeos.contracts import CaptureEvent, Connection
from lifeos.storage import StateStore


def event(connection_id: str = "con_1") -> CaptureEvent:
    return CaptureEvent.create(
        connector_id="org.lifeos.example",
        connection_id=connection_id,
        source_record_id="r1",
        source_revision="1",
        kind="note",
        occurred_at="2026-09-01T00:00:00Z",
        observed_at="2026-09-01T00:00:01Z",
        text="hello",
    )


def add_connection(store: StateStore, connection_id: str = "con_1") -> None:
    store.put_connection(
        Connection(
            connection_id=connection_id,
            connector_id="org.lifeos.example",
            settings={},
        ),
        connector_name="example",
    )


def test_queue_dedupes_leases_acks_and_retries(tmp_path: Path):
    with StateStore(tmp_path / "state.sqlite") as store:
        add_connection(store)
        item = event()
        assert store.accept_event(item, raw_path="07-raw/x.json") is True
        assert store.accept_event(item, raw_path="07-raw/x.json") is False
        leased = store.lease_events(owner="w1", limit=10)
        assert [value.event_id for value in leased] == [item.event_id]
        assert store.lease_events(owner="w2", limit=10) == []
        assert store.retry_event(item.event_id, owner="w1", error="boom", delay_seconds=0) == "queued"
        leased_again = store.lease_events(owner="w2", limit=10)
        assert [value.event_id for value in leased_again] == [item.event_id]
        assert store.ack_event(item.event_id, owner="w2") is True
        assert store.queue_counts()["processed"] == 1


def test_queue_dead_letters_after_max_attempts(tmp_path: Path):
    with StateStore(tmp_path / "state.sqlite") as store:
        add_connection(store)
        item = event()
        store.accept_event(item, raw_path=None)
        for index in range(2):
            assert store.lease_events(owner=f"w{index}")
            state = store.retry_event(
                item.event_id,
                owner=f"w{index}",
                error="bad",
                max_attempts=2,
                delay_seconds=0,
            )
        assert state == "dead"
        assert store.queue_counts()["dead"] == 1


def test_checkpoint_commits_and_purge_is_source_scoped(tmp_path: Path):
    with StateStore(tmp_path / "state.sqlite") as store:
        add_connection(store, "con_a")
        add_connection(store, "con_b")
        store.put_checkpoint("con_a", "sync", {"cursor": 3})
        store.accept_event(event("con_a"), raw_path=None)
        other = CaptureEvent.create(
            connector_id="org.lifeos.example",
            connection_id="con_b",
            source_record_id="r2",
            source_revision="1",
            kind="note",
            occurred_at="2026-09-01T00:00:00Z",
            text="other",
        )
        store.accept_event(other, raw_path=None)
        assert store.get_checkpoint("con_a", "sync") == {"cursor": 3}
        counts = store.purge_connection_data("con_a")
        assert counts["events"] == 1
        assert store.get_event(other.event_id) == other


def test_migrates_initial_skeleton_event_table(tmp_path: Path):
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE events (
          event_id TEXT PRIMARY KEY,
          connector_id TEXT NOT NULL,
          source_record_id TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          kind TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          text TEXT NOT NULL,
          deleted INTEGER NOT NULL DEFAULT 0,
          UNIQUE (connector_id, source_record_id, content_hash)
        )"""
    )
    connection.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("evt_old", "org.lifeos.example", "old", "sha256:old", "note", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", "legacy", 0),
    )
    connection.commit()
    connection.close()
    with StateStore(path) as store:
        assert store.get_event("evt_old") is not None
        assert store.stats()["events"] == 1
