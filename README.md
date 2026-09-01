# LifeOS

Open-source **evidence-to-canon world model** for a human life.

LifeOS captures noisy activity from many sources, preserves the original evidence, reduces it into typed candidate state changes, and asks the owner to promote only what makes the canonical world model more true and useful. The product is the estate, not a tiny daily checklist.

## The non-negotiable boundary

```text
providers and local capture
        ↓
immutable evidence ledger
        ↓
typed semantic compiler
        ↓
bounded subject-delta review packets
        ↓ explicit owner promotion
canonical Markdown
        ↓
rebuildable SQLite, GBrain, pgGraph, MCP, and insight views
```

Markdown is the sole canonical memory home. Agents propose. The owner promotes. Graphs, indexes, dashboards, and generated insights remain derived and lose every conflict with canon.

## What v0.2 implements

- Provider-neutral `CaptureEvent v2` with account scope, revisions, lineage, actors, conversations, attachments, metadata, and raw content references.
- Immutable SQLite evidence ledger with replay-safe processing jobs, leases, retries, dead letters, observations, candidate identities, claims, review packets, and promotion audit records.
- One ontology for people, organizations, collectives, concepts, projects, life functions, assets, places, events, decisions, and open loops.
- Predicate registry with domain/range validation, inverses, temporal behavior, cardinality, and contradiction policy.
- Exact scoped-identifier resolution with an explicit abstain state. Names nominate candidates but never establish identity.
- Spawn policies for **every major type**, not a people-only slice.
- Atomic claim reduction: repetition strengthens evidence, exclusive overlapping values raise conflicts, corrections supersede rather than overwrite.
- Four owner-facing review queues: urgent commitments, conflicts, identity/spawn decisions, and routine subject deltas.
- Owner-gated, journaled, recoverable multi-file promotion transactions.
- Canonical Markdown pages with claim-level provenance, confidence vectors, valid time, recorded time, review state, and reversible merges.
- Rebuildable SQLite and graph projections. Every graph edge carries its canonical claim ID.
- Derived relationship, self-pattern, life-function, circumstance, decision/outcome, and leverage views with input provenance and limitations.
- Local-only legacy vault scanner and migration planner. Redacted reports contain no titles, body text, identifiers, or source paths by default.
- Twelve registered connector packages: Telegram, WhatsApp Business, WhatsApp export, Gmail, IMAP, Composio, WHOOP, X, Screenpipe, Markdown folders, Google Calendar, and a synthetic example connector.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]

lifeos init ./brain
lifeos doctor
lifeos demo-compile --brain ./brain
lifeos review-list --brain ./brain
```

The demo uses synthetic data only. It shows the evidence → candidate → spawn/open-loop packet path without pretending that a proprietary model or live provider session exists.

An explicit owner action promotes a packet:

```bash
lifeos promote <packet-id> --brain ./brain --actor owner --accept-all
```

Promotion is not exposed through the read-only MCP tool registry.

## Canonical ontology

Family is not a peer type beside person. LifeOS represents it through:

1. kinship and care claims between people,
2. an optional household collective,
3. an ongoing family life function.

Likewise, founder, employee, parent, advisor, friend, and customer are roles in claims. Dashboard, map, comparison, inbox, staging, and archive are view, artifact, or workflow dimensions rather than world-object types.

See [`docs/ontology.md`](docs/ontology.md).

## Privacy

This repository contains schemas, engines, docs, and synthetic fixtures. It must not contain anyone's private wiki, raw captures, credentials, connector sessions, or migration output with private fields. Operational state lives under the private brain's `.lifeos/` directory and is ignored by Git.

## Honest status

The semantic architecture and local reference implementation are real and tested. Live provider clients remain a separate implementation pass and are not faked. The default extractor consumes typed `metadata.semantic` output from fixtures, adapters, or future local/agent extractors; the public core does not bundle a proprietary model. A rich graphical review client can sit on the packet APIs, while the owner-gated transaction boundary stays unchanged.

## Documentation

- [Architecture and brutal audit](docs/architecture.md)
- [Ontology, relations, spawn, and merge rules](docs/ontology.md)
- [Semantic compiler and contradiction handling](docs/semantic-compiler.md)
- [Review and owner promotion](docs/review-and-promotion.md)
- [Derived insight layer](docs/insights.md)
- [Private-vault migration](docs/migration.md)
- [Implementation map and acceptance bar](docs/implementation-status.md)

## Development

```bash
pip install -e .[dev]
pytest -q
```

All tests use synthetic records. No private instance data belongs in fixtures.

## License

MIT.
