# LifeOS

**Open-source capture-to-canon world model.**

LifeOS connects personal sources through plugins, accepts every capture through one durable ingest queue, turns evidence into reviewable wiki proposals, and exposes approved Markdown canon to humans and agents through GBrain, the LifeOS Intelligence Kernel, pgGraph, CLI, and MCP.

This repository is the public product. It contains no private wiki, credentials, agent souls, Guardian configuration, operator logs, or VPS paths.

## Product boundary

```text
capture plugin
    -> CaptureEvent v1
    -> durable ingest queue
    -> immutable raw evidence
    -> auto-wiki staging proposal
    -> explicit owner promotion
    -> canonical Markdown
    -> GBrain index + pgGraph projection
    -> LifeOS Intelligence Kernel
    -> CLI / MCP / tiny context packet
```

Markdown is canon. GBrain and pgGraph are derived and rebuildable. The Kernel is read-only. Capture plugins have no send, post, reply, purchase, booking, or provider-mutation methods.

## Included capture plugins

| Plugin | Capture path | Historical path | Incremental path |
|---|---|---|---|
| Telegram | owner-authorized user session | dialogs and messages | overlap-safe message polling |
| WhatsApp Business | signed Business Platform webhooks | not generally available | signed webhook inbox |
| WhatsApp export | owner-supplied `.txt` or `.zip` | export import | changed export import |
| Gmail | OAuth read-only API | message pagination | Gmail History ID |
| IMAP | read-only EXAMINE and BODY.PEEK | UID scan | UID checkpoint |
| Composio | explicit HTTPS GET endpoints | configured reads | signed trigger webhooks |
| WHOOP | OAuth read-only API | resource pagination | polling plus signed webhooks |
| X | OAuth user access | posts, mentions, optional DMs | provider cursors and IDs |
| Screenpipe | localhost Screenpipe API | bounded time-window search | high-water polling |
| Markdown folder | read-only local files | recursive scan | hashes and tombstones |
| Google Calendar | OAuth read-only API | calendar event pagination | per-calendar sync token |

Screenpipe is an integration, not a custom recorder. LifeOS does not bundle, fork, or reimplement Screenpipe and does not read its database directly.

## Status

`v0.2.0a1` is an engineering alpha.

The full provider-specific client code paths, queue, auto-wiki, promotion receipts, GBrain adapter, pgGraph projection, Kernel packet, CLI, MCP server, and webhook receiver are implemented. CI uses synthetic provider transports and fixtures. Real account authorization, provider tier behavior, historical coverage, and webhook delivery remain operator-validated facts, not release claims.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

lifeos init ./brain
lifeos --brain ./brain connector list
lifeos --brain ./brain doctor
```

Connectors accept secret **handles**, not literal credentials:

```bash
export LIFEOS_GMAIL_SECRET='{"access_token":"..."}'
lifeos --brain ./brain connector connect email-gmail \
  --secret-ref env:LIFEOS_GMAIL_SECRET \
  --config '{"query":"newer_than:2y"}'

lifeos --brain ./brain connector backfill email-gmail
lifeos --brain ./brain ingest work
lifeos --brain ./brain staging list --status awaiting_review
```

An owner promotion is explicit:

```bash
lifeos --brain ./brain staging promote prop_123 \
  --owner local-owner \
  --confirm
```

The canonical write is revision-checked and receipt-backed. GBrain and pgGraph rebuild afterward; a failed derived rebuild does not invalidate canon.

## Agents

```bash
lifeos --brain ./brain mcp serve
lifeos --brain ./brain context --purpose 'Prepare for the Atlas meeting' --entity Atlas
```

Default MCP is read-only. It can search, query, read canonical pages and entities, inspect proposals, compile a bounded Kernel packet, and read health. It cannot promote canon or act through a provider.

## Third-party plugins

Plugins register a Python entry point in the `lifeos.connectors` group and implement `lifeos.connector/v1`. Core does not need a provider branch. See `docs/plugin-authoring.md` and `schemas/connector-manifest.v1.json`.

## Documentation

- `docs/architecture.md`
- `docs/connectors.md`
- `docs/auto-wiki.md`
- `docs/retrieval-and-agents.md`
- `docs/operator-setup.md`
- `docs/plugin-authoring.md`
- `docs/threat-model.md`

## Security and privacy

LifeOS is local-first software handling unusually sensitive data. Keep secret files mode `0600`, bind the webhook server to localhost behind an explicit TLS reverse proxy, review provider scopes, test backups, and read `SECURITY.md` before connecting real accounts.

## License

MIT. External services and dependencies retain their own terms and licenses.
