# Auto-wiki and promotion

## Pipeline

1. **Raw**: the exact connector event is stored under `07-raw/` and in the
   operational event ledger.
2. **Inbox**: every processed event gets a human-readable capture record under
   `01-inbox/captures/`.
3. **Extraction**: the default extractor accepts only explicit connector actors.
   A configured command extractor may return typed candidates.
4. **Staging**: candidates become proposal files under `02-staging/` with source
   event IDs, target path, target revision, interaction count, and an editable
   canonical document.
5. **Promotion**: the owner invokes `lifeos staging promote`. LifeOS verifies
   the canonical revision, writes a prepared receipt, atomically writes Markdown,
   commits the completed receipt, and archives the proposal.
6. **Projection**: GBrain sync and pgGraph rebuild are requested after promotion.

Interaction volume controls when a page deserves another proposal. Repetition
never upgrades confidence by itself.

## Model permissions

A model may write inbox summaries, candidate entities, candidate facts,
relationships, timelines, contradictions, and proposed compiled truth under
staging.

Only the owner promotes identity merges, canonical facts, current state,
relationships, commitments, decisions, deletions, or sensitive claims.

The default MCP server has no promotion tool.
