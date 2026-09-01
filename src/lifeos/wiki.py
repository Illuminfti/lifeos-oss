"""Initialize a v2 canonical Markdown brain without private instance data."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

CANONICAL_TYPES = (
    "person",
    "organization",
    "collective",
    "concept",
    "project",
    "life-function",
    "asset",
    "place",
    "event",
    "decision",
    "open-loop",
)

TREE = [
    "CANON.md",
    "SCHEMA.md",
    "README.md",
    "schema/ontology.yaml",
    "schema/predicates.yaml",
    "schema/policies.yaml",
    "schema/json-schema/capture-event-v2.json",
    "schema/json-schema/canon-page-v2.json",
    "schema/json-schema/claim-v2.json",
    "schema/json-schema/review-packet-v2.json",
    "00-views/now.md",
    "00-views/relationships.md",
    "00-views/functions.md",
    "00-views/system-health.md",
    "01-inbox/owner/.gitkeep",
    "02-staging/packets/.gitkeep",
    "02-staging/conflicts/.gitkeep",
    "02-staging/identity/.gitkeep",
    "02-staging/merge-candidates/.gitkeep",
    *[f"03-canon/{type_id}/.gitkeep" for type_id in CANONICAL_TYPES],
    "04-artifacts/playbooks/.gitkeep",
    "04-artifacts/checklists/.gitkeep",
    "04-artifacts/templates/.gitkeep",
    "04-artifacts/authored-maps/.gitkeep",
    "04-artifacts/authored-comparisons/.gitkeep",
    "07-raw/.gitkeep",
    "99-archive/legacy-v1/.gitkeep",
    ".lifeos/.gitignore",
]

CANON = """# Canon

This directory is the sole canonical memory home for this LifeOS brain.

## Sovereignty

1. Raw evidence and operational SQLite state are not canon.
2. Structured observations and semantic proposals are not canon.
3. Agents may write typed proposals under `02-staging/` and the evidence ledger.
4. Only an explicit owner promotion transaction writes `03-canon/`.
5. GBrain, SQLite indexes, pgGraph, dashboards, and insights are rebuildable projections.
6. When a projection conflicts with canonical Markdown, canonical Markdown wins.

## Truth shape

Canon is made of durable subjects, occurrences, commitments, and atomic claims.
Relations are claims whose objects are subject references. Folders are storage
indexes, never ontology. Page prose is rendered from claims and is not parsed
back as evidence.
"""

SCHEMA = """# Schema

Machine-readable schema lives under `schema/`.

Canonical pages use `lifeos.canon-page/v2` frontmatter. Every factual assertion
is a claim with its own ID, predicate, object, modality, temporal qualifiers,
confidence vector, evidence references, review record, and lifecycle status.
There is no page-level truth confidence.

Core canonical types are: person, organization, collective, concept, project,
life_function, asset, place, event, decision, and open_loop. Family is expressed
through kinship/care relations, an optional household collective, and a family
life function. It is not a peer type beside person.
"""

README = """# LifeOS brain

Private canonical Markdown plus local evidence and projections. See `CANON.md`
and `SCHEMA.md`. This brain directory is an instance, not the public source repo.
"""

POLICIES = """schema: lifeos.policies/v2
version: 2.0.0
review:
  default_daily_packet_budget: 12
  routine_backlog_soft_limit: 50
  queues: [urgent_commitment, conflict, identity_spawn, routine_delta]
  owner_promotion_required: true
spawn:
  policy_version: spawn/v2
  canonical_merges_require_owner: true
  name_only_merge_forbidden: true
confidence:
  dimensions: [extraction, identity, evidence, temporal, modality]
projections:
  graph_is_canon: false
  markdown_wins_conflicts: true
privacy:
  public_repo_allows_private_instance_data: false
  migration_reports_redacted_by_default: true
"""

VIEW_HEADER = """---
canonical: false
generated: true
---

# {title}

This file is generated from canonical claims and structured observations. It is
not a source of truth and may be deleted and rebuilt.
"""

GITIGNORE_STATE = """*
!.gitignore
"""


def _copy_resource(destination: Path, resource_name: str) -> None:
    source = resources.files("lifeos.resources").joinpath(resource_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def init_brain(path: Path) -> Path:
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for rel in TREE:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".gitkeep"):
            if not dest.exists():
                dest.write_text("", encoding="utf-8")
        elif rel == ".lifeos/.gitignore":
            dest.write_text(GITIGNORE_STATE, encoding="utf-8")
        elif rel == "CANON.md":
            dest.write_text(CANON, encoding="utf-8")
        elif rel == "SCHEMA.md":
            dest.write_text(SCHEMA, encoding="utf-8")
        elif rel == "README.md":
            dest.write_text(README, encoding="utf-8")
        elif rel == "schema/ontology.yaml":
            _copy_resource(dest, "ontology.yaml")
        elif rel == "schema/predicates.yaml":
            _copy_resource(dest, "predicates.yaml")
        elif rel == "schema/policies.yaml":
            dest.write_text(POLICIES, encoding="utf-8")
        elif rel.startswith("schema/json-schema/"):
            _copy_resource(dest, "json-schema/" + dest.name)
        elif rel.startswith("00-views/"):
            title = dest.stem.replace("-", " ").title()
            dest.write_text(VIEW_HEADER.format(title=title), encoding="utf-8")
        elif not dest.exists():
            dest.write_text("", encoding="utf-8")
    (root / ".lifeos" / "raw" / "sha256").mkdir(parents=True, exist_ok=True)
    (root / ".lifeos" / "transactions").mkdir(parents=True, exist_ok=True)
    (root / ".lifeos" / "projections").mkdir(parents=True, exist_ok=True)
    return root
