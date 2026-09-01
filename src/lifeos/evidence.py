"""Immutable evidence ledger and replayable operational state.

Evidence is durable but noncanonical. Every semantic stage is independently
versioned, leaseable, retryable, and replay-safe. The full validated capture
envelope is retained instead of flattening an event to text.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from lifeos.contracts import CaptureEvent
from lifeos.ids import new_id
from lifeos.semantic import Mention, ProposedClaim, ReviewPacket, canonical_json, utc_now

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS evidence_event (
  event_id TEXT PRIMARY KEY,
  brain_id TEXT NOT NULL,
  connection_id TEXT NOT NULL,
  connector_id TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  source_revision TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT,
  observed_at TEXT NOT NULL,
  supersedes_event_id TEXT,
  correlation_id TEXT,
  origin_fingerprint TEXT,
  content_hash TEXT NOT NULL,
  raw_ref TEXT,
  visibility TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  tombstone INTEGER NOT NULL DEFAULT 0,
  accepted_at TEXT NOT NULL,
  UNIQUE (brain_id, connection_id, source_record_id, source_revision)
);

CREATE INDEX IF NOT EXISTS idx_evidence_connection_record
  ON evidence_event (connection_id, source_record_id);
CREATE INDEX IF NOT EXISTS idx_evidence_correlation
  ON evidence_event (correlation_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_evidence_origin
  ON evidence_event (origin_fingerprint);

CREATE TABLE IF NOT EXISTS processing_job (
  event_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  processor_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (
    state IN ('pending', 'leased', 'completed', 'failed', 'dead_letter')
  ),
  attempts INTEGER NOT NULL DEFAULT 0,
  worker_id TEXT,
  lease_until TEXT,
  completed_at TEXT,
  last_error TEXT,
  output_hash TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (event_id, stage, processor_version),
  FOREIGN KEY (event_id) REFERENCES evidence_event(event_id)
);

CREATE INDEX IF NOT EXISTS idx_processing_queue
  ON processing_job (stage, state, lease_until);

CREATE TABLE IF NOT EXISTS mention (
  mention_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  span_start INTEGER,
  span_end INTEGER,
  surface_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  proposed_type TEXT,
  role TEXT,
  identifiers_json TEXT NOT NULL,
  context_json TEXT NOT NULL,
  canonical_subject_id TEXT,
  candidate_subject_id TEXT,
  resolution_state TEXT NOT NULL DEFAULT 'unresolved',
  resolution_confidence REAL,
  extractor_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (event_id) REFERENCES evidence_event(event_id)
);

CREATE INDEX IF NOT EXISTS idx_mention_normalized
  ON mention (normalized_text, proposed_type);

CREATE TABLE IF NOT EXISTS candidate_subject (
  candidate_id TEXT PRIMARY KEY,
  proposed_type TEXT NOT NULL,
  proposed_kind TEXT,
  state TEXT NOT NULL CHECK (
    state IN ('unresolved', 'qualified', 'proposed', 'promoted', 'merged', 'rejected')
  ),
  display_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  aliases_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  independent_evidence_count INTEGER NOT NULL DEFAULT 0,
  interaction_clusters INTEGER NOT NULL DEFAULT 0,
  spawn_evidence_json TEXT NOT NULL,
  spawn_policy_version TEXT NOT NULL,
  promoted_subject_id TEXT,
  merged_into_candidate_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_name
  ON candidate_subject (normalized_name, proposed_type, state);

CREATE TABLE IF NOT EXISTS candidate_identifier (
  candidate_id TEXT NOT NULL,
  namespace TEXT NOT NULL,
  scope TEXT NOT NULL,
  value_hash TEXT NOT NULL,
  confidence REAL NOT NULL,
  evidence_event_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (candidate_id, namespace, scope, value_hash),
  FOREIGN KEY (candidate_id) REFERENCES candidate_subject(candidate_id),
  FOREIGN KEY (evidence_event_id) REFERENCES evidence_event(event_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_identifier_lookup
  ON candidate_identifier (namespace, scope, value_hash);

CREATE TABLE IF NOT EXISTS proposed_claim (
  proposed_claim_id TEXT PRIMARY KEY,
  subject_candidate_or_canon_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object_json TEXT NOT NULL,
  qualifiers_json TEXT NOT NULL,
  polarity TEXT NOT NULL,
  modality TEXT NOT NULL,
  confidence_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  state TEXT NOT NULL CHECK (
    state IN ('accumulating', 'packetized', 'promoted', 'rejected', 'expired')
  ),
  claim_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (subject_candidate_or_canon_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS proposed_claim_evidence (
  proposed_claim_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  source_span_json TEXT,
  causal_origin TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (proposed_claim_id, event_id),
  FOREIGN KEY (proposed_claim_id) REFERENCES proposed_claim(proposed_claim_id),
  FOREIGN KEY (event_id) REFERENCES evidence_event(event_id)
);

CREATE TABLE IF NOT EXISTS review_packet (
  packet_id TEXT PRIMARY KEY,
  packet_key TEXT NOT NULL UNIQUE,
  packet_kind TEXT NOT NULL CHECK (
    packet_kind IN ('urgent_commitment', 'conflict', 'identity_spawn', 'routine_delta')
  ),
  subject_id TEXT,
  priority REAL NOT NULL,
  state TEXT NOT NULL CHECK (
    state IN ('open', 'accepted', 'partially_accepted', 'rejected', 'snoozed', 'expired')
  ),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expected_review_seconds INTEGER NOT NULL,
  packet_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_priority
  ON review_packet (state, priority DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS review_action (
  action_id TEXT PRIMARY KEY,
  packet_id TEXT NOT NULL,
  action TEXT NOT NULL,
  action_json TEXT NOT NULL,
  acted_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  FOREIGN KEY (packet_id) REFERENCES review_packet(packet_id)
);

CREATE TABLE IF NOT EXISTS observation (
  observation_id TEXT PRIMARY KEY,
  metric TEXT NOT NULL,
  value REAL,
  value_json TEXT,
  unit TEXT,
  observed_at TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  dimensions_json TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  FOREIGN KEY (source_event_id) REFERENCES evidence_event(event_id)
);

CREATE INDEX IF NOT EXISTS idx_observation_metric_time
  ON observation (metric, observed_at);

CREATE TABLE IF NOT EXISTS suppression_policy (
  policy_id TEXT PRIMARY KEY,
  policy_kind TEXT NOT NULL,
  matcher_json TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS promotion_transaction (
  transaction_id TEXT PRIMARY KEY,
  packet_id TEXT,
  actor TEXT NOT NULL,
  state TEXT NOT NULL,
  expected_revisions_json TEXT NOT NULL,
  operations_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  committed_at TEXT
);

CREATE VIEW IF NOT EXISTS events AS
SELECT
  event_id,
  connector_id,
  source_record_id,
  content_hash,
  event_type AS kind,
  occurred_at,
  observed_at,
  json_extract(payload_json, '$.text') AS text,
  tombstone AS deleted
FROM evidence_event;
"""


class EvidenceRevisionConflict(RuntimeError):
    """A provider reused a scoped revision identifier for different content."""


class EvidenceStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def accept(self, event: CaptureEvent, *, origin_fingerprint: str | None = None) -> bool:
        """Store a complete immutable envelope.

        Returns False for a byte-equivalent replay of the same scoped provider
        revision. Reusing that revision for different content is a hard error.
        """
        payload = event.to_dict()
        content_hash = payload["content_hash"]
        existing = self._conn.execute(
            """SELECT event_id, content_hash FROM evidence_event
               WHERE brain_id = ? AND connection_id = ?
                 AND source_record_id = ? AND source_revision = ?""",
            (
                event.brain_id,
                event.connection_id,
                event.source_record_id,
                event.source_revision,
            ),
        ).fetchone()
        if existing is not None:
            if existing["content_hash"] == content_hash:
                return False
            raise EvidenceRevisionConflict(
                "same brain/connection/record/revision arrived with different content"
            )
        self._conn.execute(
            """INSERT INTO evidence_event (
                 event_id, brain_id, connection_id, connector_id,
                 source_record_id, source_revision, event_type, occurred_at,
                 observed_at, supersedes_event_id, correlation_id,
                 origin_fingerprint, content_hash, raw_ref, visibility,
                 sensitivity, payload_json, tombstone, accepted_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.brain_id,
                event.connection_id,
                event.connector_id,
                event.source_record_id,
                event.source_revision,
                event.kind,
                event.occurred_at,
                event.observed_at,
                event.supersedes_event_id,
                event.correlation_id,
                origin_fingerprint,
                content_hash,
                event.raw_ref,
                event.visibility,
                event.sensitivity,
                canonical_json(payload),
                int(event.deleted),
                utc_now(),
            ),
        )
        self._conn.commit()
        return True

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload_json FROM evidence_event WHERE event_id = ?", (event_id,)
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM evidence_event").fetchone()
        return int(row["n"])

    def stats(self) -> dict[str, int]:
        counts = {
            "evidence_events": self.count(),
            "pending_jobs": int(
                self._conn.execute(
                    "SELECT COUNT(*) AS n FROM processing_job WHERE state = 'pending'"
                ).fetchone()["n"]
            ),
            "open_review_packets": int(
                self._conn.execute(
                    "SELECT COUNT(*) AS n FROM review_packet WHERE state = 'open'"
                ).fetchone()["n"]
            ),
            "candidate_subjects": int(
                self._conn.execute(
                    "SELECT COUNT(*) AS n FROM candidate_subject WHERE state != 'rejected'"
                ).fetchone()["n"]
            ),
        }
        return counts

    # Processing jobs -----------------------------------------------------
    def enqueue_job(self, event_id: str, stage: str, processor_version: str) -> bool:
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO processing_job
               (event_id, stage, processor_version, state, updated_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (event_id, stage, processor_version, utc_now()),
        )
        self._conn.commit()
        return cur.rowcount == 1

    def lease_jobs(
        self,
        *,
        stage: str,
        processor_version: str,
        worker_id: str,
        limit: int = 10,
        lease_seconds: int = 60,
    ) -> list[str]:
        now = datetime.now(timezone.utc)
        now_text = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        lease_until = (now + timedelta(seconds=lease_seconds)).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        with self.transaction() as conn:
            rows = conn.execute(
                """SELECT event_id FROM processing_job
                   WHERE stage = ? AND processor_version = ?
                     AND (
                       state = 'pending'
                       OR (state = 'leased' AND lease_until < ?)
                     )
                   ORDER BY updated_at, event_id
                   LIMIT ?""",
                (stage, processor_version, now_text, max(1, int(limit))),
            ).fetchall()
            event_ids = [str(row["event_id"]) for row in rows]
            for event_id in event_ids:
                conn.execute(
                    """UPDATE processing_job
                       SET state = 'leased', worker_id = ?, lease_until = ?,
                           attempts = attempts + 1, updated_at = ?
                       WHERE event_id = ? AND stage = ? AND processor_version = ?""",
                    (
                        worker_id,
                        lease_until,
                        now_text,
                        event_id,
                        stage,
                        processor_version,
                    ),
                )
        return event_ids

    def complete_job(
        self,
        event_id: str,
        stage: str,
        processor_version: str,
        *,
        output_hash: str | None = None,
    ) -> None:
        now = utc_now()
        self._conn.execute(
            """UPDATE processing_job
               SET state = 'completed', completed_at = ?, output_hash = ?,
                   worker_id = NULL, lease_until = NULL, updated_at = ?
               WHERE event_id = ? AND stage = ? AND processor_version = ?""",
            (now, output_hash, now, event_id, stage, processor_version),
        )
        self._conn.commit()

    def fail_job(
        self,
        event_id: str,
        stage: str,
        processor_version: str,
        *,
        error: str,
        retry: bool = True,
        max_attempts: int = 5,
    ) -> str:
        row = self._conn.execute(
            """SELECT attempts FROM processing_job
               WHERE event_id = ? AND stage = ? AND processor_version = ?""",
            (event_id, stage, processor_version),
        ).fetchone()
        attempts = int(row["attempts"]) if row else max_attempts
        state = "pending" if retry and attempts < max_attempts else "dead_letter"
        self._conn.execute(
            """UPDATE processing_job
               SET state = ?, last_error = ?, worker_id = NULL,
                   lease_until = NULL, updated_at = ?
               WHERE event_id = ? AND stage = ? AND processor_version = ?""",
            (state, error[:4000], utc_now(), event_id, stage, processor_version),
        )
        self._conn.commit()
        return state

    # Mentions and identity ----------------------------------------------
    def record_mention(
        self,
        mention: Mention,
        *,
        extractor_version: str,
        candidate_subject_id: str | None = None,
        canonical_subject_id: str | None = None,
        resolution_state: str = "unresolved",
        resolution_confidence: float | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO mention (
                 mention_id, event_id, span_start, span_end, surface_text,
                 normalized_text, proposed_type, role, identifiers_json,
                 context_json, canonical_subject_id, candidate_subject_id,
                 resolution_state, resolution_confidence, extractor_version,
                 created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mention.mention_id,
                mention.event_id,
                mention.span_start,
                mention.span_end,
                mention.surface_text,
                self.normalize_name(mention.surface_text),
                mention.proposed_type,
                mention.role,
                canonical_json([item.to_dict() for item in mention.identifiers]),
                canonical_json(mention.context),
                canonical_subject_id,
                candidate_subject_id,
                resolution_state,
                resolution_confidence,
                extractor_version,
                utc_now(),
            ),
        )
        self._conn.commit()

    @staticmethod
    def normalize_name(value: str) -> str:
        return " ".join(value.casefold().strip().split())

    def create_candidate(
        self,
        *,
        proposed_type: str,
        display_name: str,
        proposed_kind: str | None,
        spawn_evidence: dict[str, Any],
        spawn_policy_version: str,
        first_seen_at: str | None = None,
        candidate_id: str | None = None,
    ) -> str:
        candidate_id = candidate_id or new_id("cand")
        now = first_seen_at or utc_now()
        self._conn.execute(
            """INSERT INTO candidate_subject (
                 candidate_id, proposed_type, proposed_kind, state,
                 display_name, normalized_name, aliases_json, first_seen_at,
                 last_seen_at, independent_evidence_count,
                 interaction_clusters, spawn_evidence_json,
                 spawn_policy_version
               ) VALUES (?, ?, ?, 'unresolved', ?, ?, '[]', ?, ?, ?, ?, ?, ?)""",
            (
                candidate_id,
                proposed_type,
                proposed_kind,
                display_name,
                self.normalize_name(display_name),
                now,
                now,
                int(spawn_evidence.get("independent_evidence_count", 0)),
                int(spawn_evidence.get("interaction_clusters", 0)),
                canonical_json(spawn_evidence),
                spawn_policy_version,
            ),
        )
        self._conn.commit()
        return candidate_id

    def update_candidate_evidence(
        self, candidate_id: str, spawn_evidence: dict[str, Any], *, state: str | None = None
    ) -> None:
        fields = [
            "spawn_evidence_json = ?",
            "independent_evidence_count = ?",
            "interaction_clusters = ?",
            "last_seen_at = ?",
        ]
        values: list[Any] = [
            canonical_json(spawn_evidence),
            int(spawn_evidence.get("independent_evidence_count", 0)),
            int(spawn_evidence.get("interaction_clusters", 0)),
            utc_now(),
        ]
        if state is not None:
            fields.append("state = ?")
            values.append(state)
        values.append(candidate_id)
        self._conn.execute(
            f"UPDATE candidate_subject SET {', '.join(fields)} WHERE candidate_id = ?", values
        )
        self._conn.commit()

    def add_candidate_identifier(
        self,
        *,
        candidate_id: str,
        namespace: str,
        scope: str,
        value_hash: str,
        confidence: float,
        event_id: str,
    ) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO candidate_identifier
               (candidate_id, namespace, scope, value_hash, confidence,
                evidence_event_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, namespace, scope, value_hash, confidence, event_id, utc_now()),
        )
        self._conn.commit()

    def find_candidates_by_identifier(
        self, *, namespace: str, scope: str, value_hash: str
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT c.* FROM candidate_subject c
               JOIN candidate_identifier i ON i.candidate_id = c.candidate_id
               WHERE i.namespace = ? AND i.scope = ? AND i.value_hash = ?
                 AND c.state NOT IN ('rejected', 'merged')""",
            (namespace, scope, value_hash),
        ).fetchall()
        return [dict(row) for row in rows]

    def find_candidates_by_name(self, name: str, proposed_type: str | None) -> list[dict[str, Any]]:
        query = """SELECT * FROM candidate_subject
                   WHERE normalized_name = ? AND state NOT IN ('rejected', 'merged')"""
        params: list[Any] = [self.normalize_name(name)]
        if proposed_type:
            query += " AND proposed_type = ?"
            params.append(proposed_type)
        return [dict(row) for row in self._conn.execute(query, params).fetchall()]

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM candidate_subject WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["spawn_evidence"] = json.loads(value.pop("spawn_evidence_json"))
        value["aliases"] = json.loads(value.pop("aliases_json"))
        return value

    def mark_candidate_promoted(self, candidate_id: str, subject_id: str) -> None:
        self._conn.execute(
            """UPDATE candidate_subject
               SET state = 'promoted', promoted_subject_id = ?, last_seen_at = ?
               WHERE candidate_id = ?""",
            (subject_id, utc_now(), candidate_id),
        )
        self._conn.commit()

    def mark_candidate_merged(self, candidate_id: str, target_candidate_id: str) -> None:
        self._conn.execute(
            """UPDATE candidate_subject
               SET state = 'merged', merged_into_candidate_id = ?, last_seen_at = ?
               WHERE candidate_id = ?""",
            (target_candidate_id, utc_now(), candidate_id),
        )
        self._conn.commit()

    # Claims --------------------------------------------------------------
    def upsert_proposed_claim(
        self, claim: ProposedClaim, *, causal_origin: str | None = None
    ) -> tuple[str, bool]:
        fingerprint = claim.fingerprint()
        now = utc_now()
        existing = self._conn.execute(
            """SELECT proposed_claim_id, claim_json FROM proposed_claim
               WHERE subject_candidate_or_canon_id = ? AND fingerprint = ?""",
            (claim.subject_id, fingerprint),
        ).fetchone()
        created = existing is None
        if created:
            claim_id = claim.proposed_claim_id
            self._conn.execute(
                """INSERT INTO proposed_claim (
                     proposed_claim_id, subject_candidate_or_canon_id,
                     subject_type, predicate, object_json, qualifiers_json,
                     polarity, modality, confidence_json, fingerprint, state,
                     claim_json, first_seen_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accumulating', ?, ?, ?)""",
                (
                    claim_id,
                    claim.subject_id,
                    claim.subject_type,
                    claim.predicate,
                    canonical_json(claim.object),
                    canonical_json(claim.qualifiers),
                    claim.polarity,
                    claim.modality,
                    canonical_json(claim.confidence.to_dict()),
                    fingerprint,
                    canonical_json(claim.to_dict()),
                    now,
                    now,
                ),
            )
        else:
            claim_id = str(existing["proposed_claim_id"])
            prior = ProposedClaim.from_dict(json.loads(existing["claim_json"]))
            merged_evidence = list(dict.fromkeys(prior.evidence_ids + claim.evidence_ids))
            prior.evidence_ids = merged_evidence
            # More independent support can strengthen, never weaken, a proposal.
            prior.confidence.evidence = max(prior.confidence.evidence, claim.confidence.evidence)
            prior.confidence.extraction = max(
                prior.confidence.extraction, claim.confidence.extraction
            )
            self._conn.execute(
                """UPDATE proposed_claim
                   SET confidence_json = ?, claim_json = ?, updated_at = ?
                   WHERE proposed_claim_id = ?""",
                (
                    canonical_json(prior.confidence.to_dict()),
                    canonical_json(prior.to_dict()),
                    now,
                    claim_id,
                ),
            )
        for event_id in claim.evidence_ids:
            self._conn.execute(
                """INSERT OR IGNORE INTO proposed_claim_evidence
                   (proposed_claim_id, event_id, causal_origin, created_at)
                   VALUES (?, ?, ?, ?)""",
                (claim_id, event_id, causal_origin, now),
            )
        self._conn.commit()
        return claim_id, created

    def list_proposed_claims(
        self, *, subject_id: str | None = None, state: str = "accumulating"
    ) -> list[ProposedClaim]:
        query = "SELECT claim_json FROM proposed_claim WHERE state = ?"
        params: list[Any] = [state]
        if subject_id is not None:
            query += " AND subject_candidate_or_canon_id = ?"
            params.append(subject_id)
        query += " ORDER BY first_seen_at, proposed_claim_id"
        return [
            ProposedClaim.from_dict(json.loads(row["claim_json"]))
            for row in self._conn.execute(query, params).fetchall()
        ]

    def set_proposed_claim_state(self, proposed_claim_id: str, state: str) -> None:
        self._conn.execute(
            "UPDATE proposed_claim SET state = ?, updated_at = ? WHERE proposed_claim_id = ?",
            (state, utc_now(), proposed_claim_id),
        )
        self._conn.commit()

    # Review packets ------------------------------------------------------
    def upsert_review_packet(self, packet: ReviewPacket) -> ReviewPacket:
        packet_key = f"{packet.packet_kind}:{packet.subject_id or 'global'}"
        existing = self._conn.execute(
            "SELECT packet_json FROM review_packet WHERE packet_key = ? AND state = 'open'",
            (packet_key,),
        ).fetchone()
        if existing:
            prior = ReviewPacket.from_dict(json.loads(existing["packet_json"]))
            by_fingerprint = {operation.fingerprint(): operation for operation in prior.operations}
            for operation in packet.operations:
                fingerprint = operation.fingerprint()
                if fingerprint in by_fingerprint:
                    old = by_fingerprint[fingerprint]
                    old.evidence_ids = list(
                        dict.fromkeys(old.evidence_ids + operation.evidence_ids)
                    )
                    if "claim" in old.payload and "claim" in operation.payload:
                        old_claim = old.payload["claim"]
                        new_claim = operation.payload["claim"]
                        old_claim["evidence_ids"] = list(
                            dict.fromkeys(
                                old_claim.get("evidence_ids", [])
                                + new_claim.get("evidence_ids", [])
                                + operation.evidence_ids
                            )
                        )
                        for key, value in new_claim.get("confidence", {}).items():
                            old_claim.setdefault("confidence", {})[key] = max(
                                float(old_claim.get("confidence", {}).get(key, 0)),
                                float(value),
                            )
                    old.priority = max(old.priority, operation.priority)
                    old.safe = old.safe and operation.safe
                else:
                    prior.operations.append(operation)
                    by_fingerprint[fingerprint] = operation
            prior.priority = max(prior.priority, packet.priority)
            prior.updated_at = utc_now()
            prior.source_event_count += packet.source_event_count
            prior.ignored_event_count += packet.ignored_event_count
            if packet.summary and packet.summary not in prior.summary:
                prior.summary = (prior.summary + "\n" + packet.summary).strip()
            packet = prior
            self._conn.execute(
                """UPDATE review_packet
                   SET priority = ?, updated_at = ?, expected_review_seconds = ?,
                       packet_json = ? WHERE packet_id = ?""",
                (
                    packet.priority,
                    packet.updated_at,
                    packet.expected_review_seconds,
                    canonical_json(packet.to_dict()),
                    packet.packet_id,
                ),
            )
        else:
            self._conn.execute(
                """INSERT INTO review_packet (
                     packet_id, packet_key, packet_kind, subject_id, priority,
                     state, created_at, updated_at, expected_review_seconds,
                     packet_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    packet.packet_id,
                    packet_key,
                    packet.packet_kind,
                    packet.subject_id,
                    packet.priority,
                    packet.state,
                    packet.created_at,
                    packet.updated_at,
                    packet.expected_review_seconds,
                    canonical_json(packet.to_dict()),
                ),
            )
        self._conn.commit()
        return packet

    def get_review_packet(self, packet_id: str) -> ReviewPacket | None:
        row = self._conn.execute(
            "SELECT packet_json FROM review_packet WHERE packet_id = ?", (packet_id,)
        ).fetchone()
        return ReviewPacket.from_dict(json.loads(row["packet_json"])) if row else None

    def list_review_packets(
        self,
        *,
        limit: int = 12,
        state: str = "open",
        packet_kind: str | None = None,
    ) -> list[ReviewPacket]:
        query = "SELECT packet_json FROM review_packet WHERE state = ?"
        params: list[Any] = [state]
        if packet_kind:
            query += " AND packet_kind = ?"
            params.append(packet_kind)
        query += " ORDER BY priority DESC, updated_at ASC LIMIT ?"
        params.append(max(1, int(limit)))
        return [
            ReviewPacket.from_dict(json.loads(row["packet_json"]))
            for row in self._conn.execute(query, params).fetchall()
        ]

    def record_review_action(
        self,
        *,
        packet_id: str,
        action: str,
        actor: str,
        details: dict[str, Any] | None = None,
        packet_state: str | None = None,
    ) -> str:
        action_id = new_id("act")
        now = utc_now()
        self._conn.execute(
            """INSERT INTO review_action
               (action_id, packet_id, action, action_json, acted_at, actor)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (action_id, packet_id, action, canonical_json(details or {}), now, actor),
        )
        if packet_state:
            packet = self.get_review_packet(packet_id)
            if packet is None:
                raise KeyError(packet_id)
            packet.state = packet_state
            packet.updated_at = now
            self._conn.execute(
                """UPDATE review_packet SET state = ?, updated_at = ?, packet_json = ?
                   WHERE packet_id = ?""",
                (packet_state, now, canonical_json(packet.to_dict()), packet_id),
            )
        self._conn.commit()
        return action_id

    # Numeric and structured observations --------------------------------
    def record_observation(
        self,
        *,
        metric: str,
        observed_at: str,
        source_event_id: str,
        value: float | None = None,
        value_json: Any | None = None,
        unit: str | None = None,
        dimensions: dict[str, Any] | None = None,
        extractor_version: str = "structured/v1",
    ) -> str:
        observation_id = new_id("obs")
        self._conn.execute(
            """INSERT INTO observation (
                 observation_id, metric, value, value_json, unit, observed_at,
                 source_event_id, dimensions_json, extractor_version
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id,
                metric,
                value,
                canonical_json(value_json) if value_json is not None else None,
                unit,
                observed_at,
                source_event_id,
                canonical_json(dimensions or {}),
                extractor_version,
            ),
        )
        self._conn.commit()
        return observation_id

    def list_observations(self, metric: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM observation WHERE metric = ?
               ORDER BY observed_at, observation_id""",
            (metric,),
        ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["dimensions"] = json.loads(value.pop("dimensions_json"))
            if value.get("value_json") is not None:
                value["value_json"] = json.loads(value["value_json"])
            values.append(value)
        return values

    # Policies and transactions ------------------------------------------
    def create_suppression_policy(
        self,
        *,
        policy_kind: str,
        matcher: dict[str, Any],
        action: str,
        reason: str,
        created_by: str,
    ) -> str:
        policy_id = new_id("pol")
        self._conn.execute(
            """INSERT INTO suppression_policy
               (policy_id, policy_kind, matcher_json, action, reason,
                created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                policy_id,
                policy_kind,
                canonical_json(matcher),
                action,
                reason,
                created_by,
                utc_now(),
            ),
        )
        self._conn.commit()
        return policy_id

    def record_promotion_transaction(
        self,
        *,
        transaction_id: str,
        packet_id: str | None,
        actor: str,
        state: str,
        expected_revisions: dict[str, int],
        operations: list[dict[str, Any]],
        committed_at: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO promotion_transaction
               (transaction_id, packet_id, actor, state,
                expected_revisions_json, operations_json, created_at,
                committed_at)
               VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                 (SELECT created_at FROM promotion_transaction WHERE transaction_id = ?), ?
               ), ?)""",
            (
                transaction_id,
                packet_id,
                actor,
                state,
                canonical_json(expected_revisions),
                canonical_json(operations),
                transaction_id,
                utc_now(),
                committed_at,
            ),
        )
        self._conn.commit()


# Backward-compatible name. The implementation is now a full evidence ledger.
IngestQueue = EvidenceStore
