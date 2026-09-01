"""Durable ingest. No LLM. Idempotent by (connector_id, source_record_id, content_hash)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from lifeos.contracts import CaptureEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
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
);
"""


class IngestQueue:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def accept(self, event: CaptureEvent) -> bool:
        """Return True if newly stored, False if duplicate."""
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO events
               (event_id, connector_id, source_record_id, content_hash, kind,
                occurred_at, observed_at, text, deleted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.connector_id,
                event.source_record_id,
                event.content_hash,
                event.kind,
                event.occurred_at,
                event.observed_at,
                event.text,
                int(event.deleted),
            ),
        )
        self._conn.commit()
        return cur.rowcount == 1

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])
