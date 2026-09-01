# Architecture

## Authority order

1. Provider evidence enters through a capture plugin.
2. The ingest queue durably accepts `CaptureEvent v1`.
3. Auto-wiki preserves raw evidence and creates staging proposals.
4. An owner explicitly promotes a revision-checked diff.
5. Canonical Markdown is written atomically and receives a promotion receipt.
6. GBrain indexes canon and pgGraph projects metadata and links.
7. The read-only LifeOS Intelligence Kernel compiles bounded evidence packets.
8. CLI and MCP expose the system without bypassing promotion or outbound approval.

Markdown is canon. Raw evidence and staging are not canon. GBrain and pgGraph are derived and can be rebuilt.

## Capture boundary

Every plugin implements `lifeos.connector/v1` and emits the same `CaptureEvent v1` envelope. A plugin owns provider authentication, pagination, source cursors, provider-specific normalization, and sanitized health. It does not own the queue, wiki, GBrain, pgGraph, Kernel, or promotion.

Core discovers built-ins and third-party Python entry points. Adding a provider does not add a provider switch to core.

Capture plugins are read-only. Outbound methods are not part of the protocol, and manifests declaring outbound actions are rejected.

## Ingest boundary

SQLite provides the first local durable implementation:

- WAL mode
- replay-safe uniqueness
- processing leases
- bounded exponential retry
- dead-letter state
- persisted connections and secret references
- backfill and incremental checkpoints
- signed-webhook inbox

The queue contains no model code and does not decide truth. A connector checkpoint advances only after events have been durably accepted.

## Auto-wiki boundary

Auto-wiki writes immutable raw manifests under `07-raw/` and typed proposal state under `.lifeos/proposals/`, mirrored as readable Markdown in `02-staging/`.

Models may draft proposal summaries and aliases. They may not edit canonical pages, merge identities, promote facts, or execute actions. Owner promotion verifies the target revision, writes atomically, records before/after content and evidence hashes, and can be reversed while the target revision remains unchanged.

## Retrieval boundary

GBrain is the search and hybrid retrieval layer. LifeOS invokes it through a thin adapter and does not invent another search product.

pgGraph stores only rebuildable graph metadata: entity ID, type, title, canonical path, revision, and link edges. It excludes raw captures, page bodies, account identifiers, and credentials. Callers hydrate selected nodes from canonical Markdown.

The LifeOS Intelligence Kernel is read-only. It admits evidence for a stated purpose, enforces an 800-token default and 2,000-token hard packet budget, reports failed source coverage, and returns a stable digest. An unchanged digest yields `not_modified` instead of another wiki dump.

## Human and agent surfaces

The CLI is the owner and operator surface for connector lifecycle, ingest work, proposal review, explicit promotion, retrieval, graph rebuild, webhooks, and MCP.

MCP is a curated read-only facade. It exposes search, query, canonical page/entity reads, staging inspection, context packets, and health. It deliberately omits direct canonical writes, promotion, credentials, arbitrary graph access, and provider actions.

## Screenpipe

Desktop audio and video capture belongs to Screenpipe. `org.lifeos.screenpipe` is a first-party LifeOS connector for Screenpipe's localhost API. It does not record, request operating-system capture permission, read Screenpipe's database, bundle Screenpipe, or copy raw frames/audio by default.

## Private estate boundary

The public product never contains or imports by default:

- Illumi's wiki contents
- credentials or provider sessions
- Guardian or Hermes configuration
- agent souls
- operator logs
- private hostnames or VPS paths
- personal policies or private fixtures
