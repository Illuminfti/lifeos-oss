# LifeOS v2 architecture

## Purpose

LifeOS exists to take cognitive load off its owner, keep the owner current, and improve circumstances through a fact-based model of people, relationships, organizations, family responsibilities, ideas, movements, projects, assets, events, decisions, commitments, and self-patterns.

The architectural problem is not capture. Capture is abundant. The hard problem is converting a torrent of source-shaped records into a small number of durable, correct, inspectable state changes without making the owner curate a second inbox.

## Sovereignty layers

1. **Raw reality**: provider payloads, exports, recordings, files, and content-addressed raw objects. Not canon.
2. **Evidence and structured observations**: immutable event envelopes, extracted metrics, mentions, and episodes. Not canon.
3. **Semantic proposals**: candidate identities, claims, conflicts, occurrences, open loops, and review packets. Not canon.
4. **Canonical Markdown**: the sole authoritative world model.
5. **Retrieval and graph projections**: SQLite, GBrain, pgGraph, search, and graph exports. Derived.
6. **Runtime consumers**: agents, MCP clients, dashboards, and insight algorithms. Consumers, never sources.

## The old loss function

The v0.1 path was effectively:

```text
capture → flatten to text → choose a bucket → guess a page → append a block → owner cleans it
```

That loses orders of magnitude in the following places:

| Failure mode | Damage |
|---|---|
| Metadata discarded at ingest | Participant, thread, attachment, revision, visibility, and reply context vanish before reasoning. |
| Connector rather than account scope | IDs from two accounts can collide or be conflated. |
| A table called a queue | No stage state, lease, retry, processor version, dead letter, or backpressure. |
| Free-text event kinds | Providers invent incompatible semantics. |
| Three bridge categories | Media, relationship, and workstream are overlapping lenses, not an ontology. |
| Routing before understanding | Existing large pages attract more unrelated captures. |
| Highest-score target selection | Similar language is confused with identity. |
| Append-only source blocks | Repetition, staleness, and contradictions become prose sediment. |
| Page-level confidence | One score falsely describes confirmed, tentative, historical, disputed, and incorrect claims together. |
| Folder-as-ontology | Moving a file appears to change what the thing is. |
| Mixed note classes | Entity, dashboard, inbox, archive, map, and decision occupy incompatible dimensions. |
| Unbounded review | The owner reviews machine intermediates rather than meaningful state changes. |
| No valid-time model | History is confused with contradiction. |
| Graph without claim lineage | Derived edges look cleaner and more certain than their sources. |

The public v0.1 code also stored a metadata field on the Python event but did not persist it to SQLite, and its auto-wiki implementation wrote a generic proposal containing raw evidence text. v2 replaces that missing middle rather than polishing the router.

## Target path

```text
provider event
→ validated immutable evidence
→ deterministic noise disposition
→ episode/context construction
→ typed mentions, speech acts, observations, and claims
→ scoped identity resolution with abstention
→ spawn-policy evaluation
→ claim deduplication and contradiction detection
→ subject-level review packets
→ explicit owner promotion transaction
→ canonical Markdown
→ deterministic projections and provenance-carrying insights
```

The owner-review complexity changes from approximately `O(raw events)` to `O(novel state changes + unresolved identities + consequential conflicts + open loops)`.

## Module map

| Module | Responsibility |
|---|---|
| `contracts.py` | Provider-neutral capture envelope. |
| `evidence.py` | Immutable ledger and operational/replay state. |
| `ontology.py` | Types and predicate validation. |
| `extract.py` | Conservative noise filter and typed extractor contract. |
| `resolve.py` | Exact scoped identifiers, candidate identity, and abstention. |
| `spawn.py` | Evidence thresholds for every canonical type. |
| `reduce.py` | Claim fingerprints, evidence aggregation, supersession, and conflicts. |
| `compiler.py` | End-to-end evidence-to-packet orchestration. Never writes canon. |
| `review.py` | Subject-delta packetization and attention backpressure. |
| `canon.py` | Canonical Markdown parser, renderer, and validator. |
| `promote.py` | Explicit owner-gated atomic transactions and recovery journals. |
| `projection.py` | Rebuildable SQLite and graph projections. |
| `insights.py` | Derived views with provenance and limitations. |
| `migration.py` | Local-only legacy scanner and migration planner. |

## Evidence contract

Every event has a `brain_id`, `connection_id`, provider-scoped record ID, revision, creation/update/deletion lineage, event time, observation time, actors, conversation context, attachments, links, sensitivity, visibility, complete metadata, a raw reference, and a core-verifiable content hash.

Connectors end at this boundary. They may not choose canonical IDs, folders, target pages, or ontology types.

## Processing semantics

Every stage is keyed by event, stage, and processor version. Work is leaseable, retryable, dead-letterable, and replay-safe. An unchanged event need not be reinterpreted merely because another canonical page changed.

A future episode builder may combine conversation windows, calendar/attendance evidence, Screenpipe sessions, health periods, travel episodes, or document sessions before model extraction. The stage contract supports that without changing canon.

## Standards posture

LifeOS borrows useful semantics without becoming an RDF product:

- CloudEvents-style provider-neutral event context.
- ActivityStreams-style actor, object, target, result, and activity time.
- PROV-style evidence and derivation lineage.
- Wikidata-style claims with qualifiers, references, rank, and status.
- JSON Schema validation for serialized contracts.
- Optional SHACL-like validation at the graph boundary.

## Core invariants

1. No private instance data is required in the public repository.
2. No agent or MCP tool can promote canon.
3. Names never auto-merge canonical identities.
4. Repeated evidence strengthens one claim.
5. Contradictions remain explicit.
6. Deletion or revocation retracts evidence support rather than silently rewriting history.
7. Canonical merges create reversible redirects.
8. Every projected edge carries a claim ID.
9. Generated insight never becomes canon without a normal owner promotion.
10. Canonical Markdown always wins a conflict with a projection.
