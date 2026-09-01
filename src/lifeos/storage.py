"""Durable operational state for LifeOS.

SQLite owns queue mechanics, connector state, proposals, and receipts. It is not
canonical knowledge. Canon remains Markdown.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping

from lifeos.contracts import (
    CaptureEvent,
    Connection,
    Proposal,
    PromotionReceipt,
    canonical_json,
    utc_now,
)
from lifeos.errors import ProposalNotFound

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS connections (
    connection_id TEXT PRIMARY KEY,
    connector_name TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    secret_ref TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS connections_connector_idx
    ON connections(connector_name, status);

CREATE TABLE IF NOT EXISTS identity_links (
    connector_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    provider_ref TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connector_id, connection_id, provider_ref),
    FOREIGN KEY (connection_id) REFERENCES connections(connection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS identity_entity_idx
    ON identity_links(entity_id);

CREATE TABLE IF NOT EXISTS checkpoints (
    connection_id TEXT NOT NULL,
    stream TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connection_id, stream),
    FOREIGN KEY (connection_id) REFERENCES connections(connection_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    connector_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    raw_path TEXT,
    queue_state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_until TEXT,
    last_error TEXT,
    processed_at TEXT,
    UNIQUE (connector_id, connection_id, source_record_id, source_revision, content_hash),
    FOREIGN KEY (connection_id) REFERENCES connections(connection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS events_queue_idx
    ON events(queue_state, available_at, observed_at);
CREATE INDEX IF NOT EXISTS events_connection_idx
    ON events(connection_id, occurred_at);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    proposal_type TEXT NOT NULL,
    status TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    target_path TEXT NOT NULL,
    target_revision TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    staging_path TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY (connection_id) REFERENCES connections(connection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS proposals_status_idx
    ON proposals(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS proposals_active_target_idx
    ON proposals(proposal_type, target_path, connection_id)
    WHERE status = 'awaiting_review';

CREATE TABLE IF NOT EXISTS promotion_receipts (
    receipt_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    target_path TEXT NOT NULL,
    before_revision TEXT NOT NULL,
    after_revision TEXT NOT NULL,
    promoted_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
    webhook_id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    headers_json TEXT NOT NULL,
    body_json TEXT NOT NULL,
    processed_at TEXT,
    FOREIGN KEY (connection_id) REFERENCES connections(connection_id) ON DELETE CASCADE
);
"""


def _json(value: Any) -> str:
    return canonical_json(value)


def _parse(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _future(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        # The first public skeleton created an `events` table without a schema
        # marker or connection/revision columns. Preserve it rather than letting
        # CREATE TABLE IF NOT EXISTS hide an incompatible shape.
        existing = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()
        legacy_rows: list[sqlite3.Row] = []
        if existing:
            columns = {str(row[1]) for row in self.conn.execute("PRAGMA table_info(events)")}
            if "connection_id" not in columns:
                legacy_rows = list(self.conn.execute("SELECT * FROM events"))
                self.conn.execute("ALTER TABLE events RENAME TO events_legacy_0_1")

        # sqlite3.executescript manages its own transaction boundary, so it must
        # not run inside `transaction()` (it would leave no active transaction
        # for the context manager to commit).
        self.conn.executescript(DDL)
        row = self.conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
        elif int(row[0]) != SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported state schema {row[0]}; expected {SCHEMA_VERSION}"
            )

        if legacy_rows:
            from uuid import NAMESPACE_URL, uuid5

            connections: dict[str, str] = {}
            now = utc_now()
            for legacy in legacy_rows:
                connector_id = str(legacy["connector_id"])
                connection_id = connections.setdefault(
                    connector_id,
                    "con_legacy_" + uuid5(NAMESPACE_URL, connector_id).hex[:16],
                )
                self.conn.execute(
                    """INSERT OR IGNORE INTO connections(
                        connection_id, connector_name, connector_id, settings_json,
                        scopes_json, secret_ref, status, created_at, updated_at
                    ) VALUES (?, ?, ?, '{}', '[]', NULL, 'legacy', ?, ?)""",
                    (connection_id, "legacy", connector_id, now, now),
                )
                event = CaptureEvent(
                    event_id=str(legacy["event_id"]),
                    connector_id=connector_id,
                    connection_id=connection_id,
                    source_record_id=str(legacy["source_record_id"]),
                    source_revision=str(legacy["content_hash"]),
                    kind=str(legacy["kind"]),
                    occurred_at=str(legacy["occurred_at"]),
                    observed_at=str(legacy["observed_at"]),
                    text=str(legacy["text"]),
                    deleted=bool(legacy["deleted"]),
                    content_hash=str(legacy["content_hash"]),
                )
                self.accept_event(event, raw_path=None)
            self.conn.execute("DROP TABLE events_legacy_0_1")

    # Connections ---------------------------------------------------------
    def put_connection(self, connection: Connection, *, connector_name: str) -> None:
        self.conn.execute(
            """
            INSERT INTO connections(
                connection_id, connector_name, connector_id, settings_json,
                scopes_json, secret_ref, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                connector_name=excluded.connector_name,
                connector_id=excluded.connector_id,
                settings_json=excluded.settings_json,
                scopes_json=excluded.scopes_json,
                secret_ref=excluded.secret_ref,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                connection.connection_id,
                connector_name,
                connection.connector_id,
                _json(connection.settings),
                _json(list(connection.granted_scopes)),
                connection.secret_ref,
                connection.status,
                connection.created_at,
                connection.updated_at,
            ),
        )

    def get_connection(self, connection_id: str) -> tuple[str, Connection] | None:
        row = self.conn.execute(
            "SELECT * FROM connections WHERE connection_id = ?", (connection_id,)
        ).fetchone()
        if row is None:
            return None
        return str(row["connector_name"]), Connection(
            connection_id=str(row["connection_id"]),
            connector_id=str(row["connector_id"]),
            settings=_parse(row["settings_json"], {}),
            granted_scopes=tuple(_parse(row["scopes_json"], [])),
            secret_ref=row["secret_ref"],
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_connections(self, *, include_revoked: bool = False) -> list[tuple[str, Connection]]:
        sql = "SELECT * FROM connections"
        params: tuple[Any, ...] = ()
        if not include_revoked:
            sql += " WHERE status != ?"
            params = ("revoked",)
        sql += " ORDER BY created_at, connection_id"
        rows = self.conn.execute(sql, params).fetchall()
        result: list[tuple[str, Connection]] = []
        for row in rows:
            found = self.get_connection(str(row["connection_id"]))
            if found:
                result.append(found)
        return result

    def set_connection_status(self, connection_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE connections SET status = ?, updated_at = ? WHERE connection_id = ?",
            (status, utc_now(), connection_id),
        )

    def delete_connection(self, connection_id: str) -> None:
        self.conn.execute("DELETE FROM connections WHERE connection_id = ?", (connection_id,))

    # Private identity map ------------------------------------------------
    def get_identity(
        self, *, connector_id: str, connection_id: str, provider_ref: str
    ) -> str | None:
        row = self.conn.execute(
            """SELECT entity_id FROM identity_links
               WHERE connector_id=? AND connection_id=? AND provider_ref=?""",
            (connector_id, connection_id, provider_ref),
        ).fetchone()
        return str(row[0]) if row else None

    def put_identity(
        self,
        *,
        connector_id: str,
        connection_id: str,
        provider_ref: str,
        entity_id: str,
        display_name: str,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO identity_links(
                connector_id, connection_id, provider_ref, entity_id,
                display_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(connector_id, connection_id, provider_ref) DO UPDATE SET
                display_name=excluded.display_name,
                updated_at=excluded.updated_at
            """,
            (
                connector_id, connection_id, provider_ref, entity_id,
                display_name, now, now,
            ),
        )

    def identities_for_entity(self, entity_id: str) -> list[dict[str, str]]:
        rows = self.conn.execute(
            """SELECT connector_id, connection_id, provider_ref, display_name
               FROM identity_links WHERE entity_id=? ORDER BY created_at""",
            (entity_id,),
        ).fetchall()
        return [
            {
                "connector_id": str(row[0]),
                "connection_id": str(row[1]),
                "provider_ref": str(row[2]),
                "display_name": str(row[3]),
            }
            for row in rows
        ]

    # Checkpoints ---------------------------------------------------------
    def get_checkpoint(self, connection_id: str, stream: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT checkpoint_json FROM checkpoints WHERE connection_id=? AND stream=?",
            (connection_id, stream),
        ).fetchone()
        return dict(_parse(row[0], {})) if row else {}

    def put_checkpoint(
        self, connection_id: str, stream: str, checkpoint: Mapping[str, Any]
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO checkpoints(connection_id, stream, checkpoint_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(connection_id, stream) DO UPDATE SET
                checkpoint_json=excluded.checkpoint_json,
                updated_at=excluded.updated_at
            """,
            (connection_id, stream, _json(checkpoint), utc_now()),
        )

    # Events and queue ----------------------------------------------------
    def accept_event(self, event: CaptureEvent, *, raw_path: str | None) -> bool:
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO events(
                event_id, connector_id, connection_id, source_record_id,
                source_revision, content_hash, kind, occurred_at, observed_at,
                payload_json, raw_path, queue_state, attempts, available_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)
            """,
            (
                event.event_id,
                event.connector_id,
                event.connection_id,
                event.source_record_id,
                event.source_revision,
                event.content_hash,
                event.kind,
                event.occurred_at,
                event.observed_at,
                _json(event.to_dict()),
                raw_path,
                utc_now(),
            ),
        )
        return cursor.rowcount == 1

    def get_event(self, event_id: str) -> CaptureEvent | None:
        row = self.conn.execute(
            "SELECT payload_json FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return CaptureEvent.from_dict(_parse(row[0], {})) if row else None

    def lease_events(
        self, *, owner: str, limit: int = 50, lease_seconds: int = 60
    ) -> list[CaptureEvent]:
        now = utc_now()
        until = _future(lease_seconds)
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE events
                SET queue_state='queued', lease_owner=NULL, lease_until=NULL
                WHERE queue_state='processing' AND lease_until < ?
                """,
                (now,),
            )
            rows = conn.execute(
                """
                SELECT event_id, payload_json FROM events
                WHERE queue_state='queued' AND available_at <= ?
                ORDER BY occurred_at, observed_at, event_id
                LIMIT ?
                """,
                (now, max(1, limit)),
            ).fetchall()
            ids = [str(row["event_id"]) for row in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                conn.execute(
                    f"""UPDATE events
                    SET queue_state='processing', lease_owner=?, lease_until=?
                    WHERE event_id IN ({marks})""",
                    (owner, until, *ids),
                )
        return [CaptureEvent.from_dict(_parse(row["payload_json"], {})) for row in rows]

    def ack_event(self, event_id: str, *, owner: str) -> bool:
        cursor = self.conn.execute(
            """
            UPDATE events SET queue_state='processed', processed_at=?,
                lease_owner=NULL, lease_until=NULL, last_error=NULL
            WHERE event_id=? AND queue_state='processing' AND lease_owner=?
            """,
            (utc_now(), event_id, owner),
        )
        return cursor.rowcount == 1

    def retry_event(
        self,
        event_id: str,
        *,
        owner: str,
        error: str,
        max_attempts: int = 5,
        delay_seconds: int | None = None,
    ) -> str:
        row = self.conn.execute(
            "SELECT attempts FROM events WHERE event_id=? AND lease_owner=?",
            (event_id, owner),
        ).fetchone()
        if row is None:
            return "missing"
        attempts = int(row[0]) + 1
        if attempts >= max_attempts:
            state = "dead"
            available = utc_now()
        else:
            state = "queued"
            delay = delay_seconds if delay_seconds is not None else min(3600, 2**attempts)
            available = _future(delay)
        self.conn.execute(
            """
            UPDATE events SET queue_state=?, attempts=?, available_at=?,
                lease_owner=NULL, lease_until=NULL, last_error=?
            WHERE event_id=? AND lease_owner=?
            """,
            (state, attempts, available, error[:4000], event_id, owner),
        )
        return state

    def queue_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT queue_state, COUNT(*) AS n FROM events GROUP BY queue_state"
        ).fetchall()
        result = {"queued": 0, "processing": 0, "processed": 0, "dead": 0}
        result.update({str(row[0]): int(row[1]) for row in rows})
        return result

    def list_events(
        self,
        *,
        connection_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[CaptureEvent]:
        where: list[str] = []
        params: list[Any] = []
        if connection_id:
            where.append("connection_id=?")
            params.append(connection_id)
        if state:
            where.append("queue_state=?")
            params.append(state)
        sql = "SELECT payload_json FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY occurred_at DESC, event_id DESC LIMIT ?"
        params.append(max(1, limit))
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [CaptureEvent.from_dict(_parse(row[0], {})) for row in rows]

    # Proposals -----------------------------------------------------------
    def put_proposal(self, proposal: Proposal, *, payload: Mapping[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO proposals(
                proposal_id, proposal_type, status, connector_id, connection_id,
                target_path, target_revision, title, summary, evidence_json,
                staging_path, payload_json, created_at, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proposal_id) DO UPDATE SET
                status=excluded.status,
                summary=excluded.summary,
                evidence_json=excluded.evidence_json,
                staging_path=excluded.staging_path,
                payload_json=excluded.payload_json,
                reviewed_at=excluded.reviewed_at
            """,
            (
                proposal.proposal_id,
                proposal.proposal_type,
                proposal.status,
                proposal.connector_id,
                proposal.connection_id,
                proposal.target_path,
                proposal.target_revision,
                proposal.title,
                proposal.summary,
                _json(list(proposal.evidence_event_ids)),
                proposal.staging_path,
                _json(payload),
                proposal.created_at,
                proposal.reviewed_at,
            ),
        )

    def get_proposal(self, proposal_id: str) -> tuple[Proposal, dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ProposalNotFound(proposal_id)
        proposal = Proposal(
            proposal_id=str(row["proposal_id"]),
            proposal_type=str(row["proposal_type"]),
            status=str(row["status"]),
            connector_id=str(row["connector_id"]),
            connection_id=str(row["connection_id"]),
            target_path=str(row["target_path"]),
            target_revision=str(row["target_revision"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            evidence_event_ids=tuple(_parse(row["evidence_json"], [])),
            staging_path=str(row["staging_path"]),
            created_at=str(row["created_at"]),
            reviewed_at=row["reviewed_at"],
        )
        return proposal, dict(_parse(row["payload_json"], {}))

    def find_active_proposal(
        self, *, proposal_type: str, target_path: str, connection_id: str
    ) -> tuple[Proposal, dict[str, Any]] | None:
        row = self.conn.execute(
            """SELECT proposal_id FROM proposals
               WHERE proposal_type=? AND target_path=? AND connection_id=?
                 AND status='awaiting_review'
               LIMIT 1""",
            (proposal_type, target_path, connection_id),
        ).fetchone()
        return self.get_proposal(str(row[0])) if row else None

    def list_proposals(
        self, *, status: str | None = "awaiting_review", limit: int = 100
    ) -> list[Proposal]:
        if status is None:
            rows = self.conn.execute(
                "SELECT proposal_id FROM proposals ORDER BY created_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT proposal_id FROM proposals WHERE status=?
                   ORDER BY created_at DESC LIMIT ?""",
                (status, max(1, limit)),
            ).fetchall()
        return [self.get_proposal(str(row[0]))[0] for row in rows]

    def set_proposal_status(self, proposal_id: str, status: str) -> None:
        cursor = self.conn.execute(
            "UPDATE proposals SET status=?, reviewed_at=? WHERE proposal_id=?",
            (status, utc_now(), proposal_id),
        )
        if cursor.rowcount != 1:
            raise ProposalNotFound(proposal_id)

    def add_promotion_receipt(
        self, receipt: PromotionReceipt, *, payload: Mapping[str, Any]
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO promotion_receipts(
                receipt_id, proposal_id, reviewer, target_path,
                before_revision, after_revision, promoted_at,
                evidence_json, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.receipt_id,
                receipt.proposal_id,
                receipt.reviewer,
                receipt.target_path,
                receipt.before_revision,
                receipt.after_revision,
                receipt.promoted_at,
                _json(list(receipt.evidence_event_ids)),
                _json(payload),
            ),
        )

    def list_receipts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT payload_json FROM promotion_receipts ORDER BY promoted_at DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [dict(_parse(row[0], {})) for row in rows]

    # Webhooks ------------------------------------------------------------
    def add_webhook(
        self,
        connection_id: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO webhook_events(
                connection_id, received_at, headers_json, body_json
            ) VALUES (?, ?, ?, ?)""",
            (connection_id, utc_now(), _json(headers), _json(body)),
        )
        return int(cursor.lastrowid)

    def pending_webhooks(self, connection_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM webhook_events
               WHERE connection_id=? AND processed_at IS NULL
               ORDER BY webhook_id LIMIT ?""",
            (connection_id, max(1, limit)),
        ).fetchall()
        return [
            {
                "webhook_id": int(row["webhook_id"]),
                "received_at": str(row["received_at"]),
                "headers": _parse(row["headers_json"], {}),
                "body": _parse(row["body_json"], {}),
            }
            for row in rows
        ]

    def ack_webhooks(self, ids: Iterable[int]) -> None:
        values = [int(value) for value in ids]
        if not values:
            return
        marks = ",".join("?" for _ in values)
        self.conn.execute(
            f"UPDATE webhook_events SET processed_at=? WHERE webhook_id IN ({marks})",
            (utc_now(), *values),
        )

    # Purge ---------------------------------------------------------------
    def purge_connection_data(self, connection_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.transaction() as conn:
            for table in ("events", "proposals", "checkpoints", "webhook_events", "identity_links"):
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE connection_id=?", (connection_id,)
                ).fetchone()
                counts[table] = int(row[0]) if row else 0
                conn.execute(f"DELETE FROM {table} WHERE connection_id=?", (connection_id,))
        return counts

    def stats(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "connections": int(self.conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0]),
            "events": int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
            "proposals": int(self.conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]),
            "receipts": int(
                self.conn.execute("SELECT COUNT(*) FROM promotion_receipts").fetchone()[0]
            ),
            "queue": self.queue_counts(),
        }
