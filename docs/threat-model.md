# Threat model

LifeOS aggregates messages, documents, meetings, health records, screen context, identities, and derived claims. A compromise can expose a larger and more coherent picture than any single source. Local-first is a custody choice, not an automatic security guarantee.

## Assets

- provider credentials and sessions
- raw captures and attachments
- canonical Markdown
- identity mappings
- staging proposals and rejected claims
- sync cursors and webhook payloads
- promotion and reversal receipts
- GBrain and pgGraph projections
- context packets delivered to agents or models

## Trust boundaries

1. provider API or owner-selected local source
2. connector plugin process and dependency chain
3. secret resolver
4. durable ingest and webhook inbox
5. auto-wiki model boundary
6. owner promotion boundary
7. GBrain and pgGraph derived stores
8. CLI and MCP clients
9. reverse proxy for public webhook delivery

## Principal threats and controls

### Credential disclosure

Controls:

- only `env:` and absolute `file:` secret handles
- POSIX secret files must be mode `0600` or stricter
- secret values never enter Markdown, events, receipts, health, or MCP
- webhook logs suppress bodies, signatures, and challenge tokens
- revoke clears persisted secret references

Remaining risk: environment variables and local files are readable by sufficiently privileged local processes.

### Malicious or compromised plugin

Controls:

- capture manifests must declare `outbound_actions=false`
- core exposes a narrow context and common event contract
- third-party entry points are explicit installed code, not remote scripts
- provider-independent queue and promotion boundaries
- conformance tests and dependency review

Remaining risk: a Python plugin runs in the LifeOS process in this alpha. Strong process sandboxing and signed plugin distribution are not yet implemented.

### Provider payload injection

Controls:

- raw evidence remains non-canonical
- deterministic normalization precedes model use
- model output is restricted to staging fields
- owner promotion is explicit and revision-checked
- every promoted claim retains evidence hashes

Remaining risk: an owner can still approve a persuasive malicious proposal. Review UX and sensitive-claim policy require continued hardening.

### Webhook forgery or replay

Controls:

- connector-specific signature verification over the exact raw body
- provider event IDs and unique durable webhook rows
- body-size limit
- localhost-only server by default
- acknowledgment only after normalized events are durably accepted

Remaining risk: proxy misconfiguration can alter bodies, strip signatures, or expose routes.

### Silent staleness

Controls:

- typed health states
- persisted checkpoints
- full-resync errors for expired Gmail/Calendar cursors
- explicit incomplete backfill warnings
- Kernel coverage includes failed sources
- derived rebuild failure does not masquerade as current canon

Remaining risk: some providers do not expose every deletion or historical record. Connector docs state those limits.

### Canon corruption

Controls:

- agents and connectors cannot write canon
- promotion requires explicit confirmation and owner identity
- target revision comparison prevents stale overwrite
- atomic file replacement
- before/after promotion receipt
- guarded reversal

Remaining risk: filesystem-level writes outside LifeOS can still alter Markdown. Backups, file permissions, and independent integrity monitoring remain operator responsibilities.

### Oversharing to agents or models

Controls:

- MCP is read-only and cannot access `.lifeos/` or raw folders through `get_page`
- Kernel packets are purpose-scoped, cited, digestible, and bounded to 2,000 tokens
- no default wiki dump
- pgGraph excludes bodies and credentials

Remaining risk: the configured model provider may receive excerpts selected for proposal generation or retrieval. Model-provider custody and redaction policy remain operator decisions.

### Screen capture overcollection

Controls:

- Screenpipe owns recording and operating-system permissions
- LifeOS only calls its API
- enabled content classes are explicit
- remote Screenpipe hosts require explicit opt-in
- frames and audio files are not copied by default
- purge affects LifeOS data, not Screenpipe's independent store

Remaining risk: Screenpipe retention and capture policy are separate operator responsibilities.

## Explicit non-goals in this alpha

- hostile multi-tenant isolation
- untrusted plugin sandboxing
- managed public OAuth applications
- transparent database encryption
- mobile platform hardening
- outbound Action Plane
- automatic promotion of sensitive claims
- protection from a fully compromised owner account or operating system
