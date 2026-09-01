# Data custody

## Canon

Canonical Markdown is portable, readable, and owner-editable. It contains
reviewed knowledge and provenance references, not connector credentials or queue
state.

## Operational state

`.lifeos/` contains:

- SQLite queue, connections, checkpoints, identity links, proposals, and receipts
- connector sessions and local webhook state
- a mode `0600` secret file for the built-in backend
- private receipt archives

This directory is ignored by Git and excluded from GBrain imports.

## Raw evidence

`07-raw/` may contain sensitive provider payloads. It belongs to the owner's
brain and is never included in this public repository. Source-scoped purge removes
raw evidence and derived operational records, then creates a canonical review
item instead of silently deleting approved claims.

## Built-in secret backend

The built-in backend is intentionally transparent: a local owner-only JSON file.
It is not encrypted by LifeOS. Full-disk encryption or a replacement `SecretStore`
backed by a keychain or external vault is required for stronger at-rest custody.
