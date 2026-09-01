# Retrieval and agent access

## Canon and derived state

Canonical Markdown is the only approved current account. Retrieval systems may be stale, incomplete, or unavailable. They never overrule the file revision that promotion wrote.

## GBrain

LifeOS uses GBrain for Markdown synchronization, search, and query. `GBrainAdapter` invokes the installed GBrain CLI and returns a typed failure when it is absent or unhealthy. LifeOS does not ship a hidden fallback index under another name.

```bash
lifeos --brain ./brain gbrain sync
lifeos --brain ./brain search "Atlas"
lifeos --brain ./brain query "What changed in Atlas?"
```

## pgGraph

pgGraph is an optional derived projection. It stores only:

- canonical entity ID
- type
- title
- canonical path
- page revision
- wiki-link edges

It excludes raw capture bodies, page bodies, credentials, account identifiers, and provider sessions. With no `LIFEOS_PGGRAPH_DSN`, rebuild reports `not_configured` and leaves canon untouched.

## LifeOS Intelligence Kernel

The Kernel compiles a purpose-scoped packet from GBrain results. It is read-only and bounded:

- default budget: 800 tokens
- hard maximum: 2,000 tokens
- current facts with evidence references
- source coverage and failed sources
- stable digest
- `not_modified` when the caller already holds the current digest

```bash
lifeos --brain ./brain context \
  --purpose "Prepare for the Atlas meeting" \
  --entity Atlas
```

A caller states a purpose and named subjects. It does not receive the wiki or unrestricted raw-source access.

## MCP

```bash
lifeos --brain ./brain mcp serve
```

Default tools:

- `lifeos.search`
- `lifeos.query`
- `lifeos.get_page`
- `lifeos.get_entity`
- `lifeos.context`
- `lifeos.list_proposals`
- `lifeos.get_proposal`
- `lifeos.sources`
- `lifeos.connector_health`
- `lifeos.system_health`

The server uses newline-delimited JSON-RPC over stdio. It supports MCP initialize, tool discovery, tool calls, and ping.

## Deliberately absent

The default agent interface has no:

- canonical `put_page`
- canonical delete
- owner promotion
- identity merge
- credential read
- connector installation
- arbitrary pgGraph query
- provider send, post, reply, like, follow, delete, purchase, or booking

An agent can inspect and explain a staging proposal. It cannot turn its own draft into truth.
