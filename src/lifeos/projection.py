"""Disposable SQLite and graph projections rebuilt from canonical Markdown."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from lifeos.canon import CanonicalVault, claim_fingerprint, render_markdown
from lifeos.semantic import canonical_json, utc_now

PROJECTION_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE projection_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE canon_subject (
  subject_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  kind TEXT,
  title TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL,
  sensitivity TEXT NOT NULL,
  importance REAL NOT NULL,
  page_hash TEXT NOT NULL
);

CREATE TABLE canon_claim (
  claim_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  object_ref TEXT,
  object_type TEXT,
  object_json TEXT NOT NULL,
  polarity TEXT NOT NULL,
  modality TEXT NOT NULL,
  qualifiers_json TEXT NOT NULL,
  status TEXT NOT NULL,
  rank TEXT NOT NULL,
  confidence_json TEXT NOT NULL,
  asserted_at TEXT,
  valid_from TEXT,
  valid_to TEXT,
  recorded_at TEXT NOT NULL,
  supersedes_claim_id TEXT,
  sensitivity TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  FOREIGN KEY (subject_id) REFERENCES canon_subject(subject_id)
);

CREATE INDEX idx_claim_subject_predicate
  ON canon_claim (subject_id, predicate, status);
CREATE INDEX idx_claim_object_ref
  ON canon_claim (object_ref, predicate, status);
CREATE INDEX idx_claim_validity
  ON canon_claim (valid_from, valid_to);

CREATE TABLE canon_claim_evidence (
  claim_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  PRIMARY KEY (claim_id, evidence_id),
  FOREIGN KEY (claim_id) REFERENCES canon_claim(claim_id)
);

CREATE VIRTUAL TABLE canon_search USING fts5(
  subject_id UNINDEXED,
  title,
  aliases,
  claim_text
);

CREATE VIEW canon_relation AS
SELECT
  claim_id,
  subject_id,
  predicate,
  object_ref,
  object_type,
  qualifiers_json,
  status,
  rank,
  confidence_json,
  valid_from,
  valid_to
FROM canon_claim
WHERE object_kind = 'entity_ref'
  AND object_ref IS NOT NULL;
"""


class ProjectionBuilder:
    VERSION = "projection/v2"

    def __init__(self, vault: CanonicalVault, index_path: Path | None = None):
        self.vault = vault
        self.index_path = Path(index_path or (vault.root / ".lifeos" / "index.sqlite"))

    def rebuild(self) -> dict[str, Any]:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.index_path.with_suffix(".sqlite.tmp")
        if temp.exists():
            temp.unlink()
        conn = sqlite3.connect(temp)
        try:
            conn.executescript(PROJECTION_SCHEMA)
            page_count = 0
            claim_count = 0
            relation_count = 0
            for page in self.vault.iter_pages():
                front = page.frontmatter
                text = render_markdown(front, page.body)
                page_hash = sha256(text.encode("utf-8")).hexdigest()
                conn.execute(
                    """INSERT INTO canon_subject (
                         subject_id, type, kind, title, path, status, revision,
                         sensitivity, importance, page_hash
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        page.id,
                        front["type"],
                        front.get("kind"),
                        front["title"],
                        page.path.relative_to(self.vault.root).as_posix(),
                        front["status"],
                        int(front["revision"]),
                        front["sensitivity"],
                        float(front.get("importance", 0.5)),
                        page_hash,
                    ),
                )
                claim_text: list[str] = []
                for claim in front.get("claims", []):
                    object_value = claim["object"]
                    object_kind = "entity_ref" if "ref" in object_value else "literal"
                    object_ref = object_value.get("ref")
                    object_type = object_value.get("type") or object_value.get("datatype")
                    qualifiers = claim.get("qualifiers", {})
                    fingerprint = claim_fingerprint(claim, page.id)
                    conn.execute(
                        """INSERT INTO canon_claim (
                             claim_id, subject_id, predicate, object_kind,
                             object_ref, object_type, object_json, polarity,
                             modality, qualifiers_json, status, rank,
                             confidence_json, asserted_at, valid_from, valid_to,
                             recorded_at, supersedes_claim_id, sensitivity,
                             fingerprint
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            claim["id"],
                            page.id,
                            claim["predicate"],
                            object_kind,
                            object_ref,
                            object_type,
                            canonical_json(object_value),
                            claim.get("polarity", "positive"),
                            claim.get("modality", "actual"),
                            canonical_json(qualifiers),
                            claim.get("status", "active"),
                            claim.get("rank", "normal"),
                            canonical_json(claim.get("confidence", {})),
                            claim.get("asserted_at"),
                            qualifiers.get("valid_from"),
                            qualifiers.get("valid_to"),
                            claim["recorded_at"],
                            claim.get("supersedes"),
                            claim.get("sensitivity", front["sensitivity"]),
                            fingerprint,
                        ),
                    )
                    for evidence_id in claim.get("evidence", []):
                        conn.execute(
                            "INSERT INTO canon_claim_evidence (claim_id, evidence_id) VALUES (?, ?)",
                            (claim["id"], evidence_id),
                        )
                    claim_text.append(
                        f"{claim['predicate']} {object_value.get('value', object_value.get('ref', object_value.get('state', '')))}"
                    )
                    claim_count += 1
                    relation_count += int(object_kind == "entity_ref")
                conn.execute(
                    "INSERT INTO canon_search (subject_id, title, aliases, claim_text) VALUES (?, ?, ?, ?)",
                    (
                        page.id,
                        front["title"],
                        " ".join(front.get("aliases", [])),
                        " ".join(claim_text),
                    ),
                )
                page_count += 1
            revision_hash = self.vault.revision_hash()
            meta = {
                "projection_version": self.VERSION,
                "built_at": utc_now(),
                "canon_revision_hash": revision_hash,
                "subjects": str(page_count),
                "claims": str(claim_count),
                "relations": str(relation_count),
            }
            conn.executemany(
                "INSERT INTO projection_meta (key, value) VALUES (?, ?)", sorted(meta.items())
            )
            conn.commit()
        finally:
            conn.close()
        os.replace(temp, self.index_path)
        return {
            "subjects": page_count,
            "claims": claim_count,
            "relations": relation_count,
            "canon_revision_hash": revision_hash,
            "path": str(self.index_path),
        }

    def export_graph_jsonl(self, destination: Path) -> dict[str, int]:
        reader = ProjectionReader(self.index_path)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        nodes = 0
        edges = 0
        with destination.open("w", encoding="utf-8") as handle:
            for subject in reader.subjects():
                record = {
                    "record_kind": "node",
                    "namespace": "canon",
                    "id": subject["subject_id"],
                    "type": subject["type"],
                    "kind": subject["kind"],
                    "title": subject["title"],
                    "status": subject["status"],
                    "revision": subject["revision"],
                    "sensitivity": subject["sensitivity"],
                }
                handle.write(canonical_json(record) + "\n")
                nodes += 1
            for relation in reader.relations():
                record = {
                    "record_kind": "edge",
                    "namespace": "canon",
                    "id": relation["claim_id"],
                    "from": relation["subject_id"],
                    "predicate": relation["predicate"],
                    "to": relation["object_ref"],
                    "object_type": relation["object_type"],
                    "qualifiers": json.loads(relation["qualifiers_json"]),
                    "status": relation["status"],
                    "rank": relation["rank"],
                    "confidence": json.loads(relation["confidence_json"]),
                    "valid_from": relation["valid_from"],
                    "valid_to": relation["valid_to"],
                }
                handle.write(canonical_json(record) + "\n")
                edges += 1
        return {"nodes": nodes, "edges": edges}


class ProjectionReader:
    def __init__(self, index_path: Path):
        self.index_path = Path(index_path)

    def _connect(self) -> sqlite3.Connection:
        if not self.index_path.exists():
            raise FileNotFoundError(self.index_path)
        conn = sqlite3.connect(self.index_path)
        conn.row_factory = sqlite3.Row
        return conn

    def metadata(self) -> dict[str, str]:
        with self._connect() as conn:
            return {
                str(row["key"]): str(row["value"])
                for row in conn.execute("SELECT key, value FROM projection_meta")
            }

    def subjects(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM canon_subject ORDER BY subject_id")]

    def relations(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM canon_relation ORDER BY claim_id")]

    def get_claims(self, subject_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM canon_claim WHERE subject_id = ? ORDER BY recorded_at, claim_id",
                (subject_id,),
            ).fetchall()
        claims: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["object"] = json.loads(value.pop("object_json"))
            value["qualifiers"] = json.loads(value.pop("qualifiers_json"))
            value["confidence"] = json.loads(value.pop("confidence_json"))
            value["id"] = value.pop("claim_id")
            claims.append(value)
        return claims

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT s.*, bm25(canon_search) AS score
                   FROM canon_search JOIN canon_subject s USING (subject_id)
                   WHERE canon_search MATCH ?
                   ORDER BY score LIMIT ?""",
                (query, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]
