# LifeOS

**An open-source, self-hostable capture-to-canon world model.**

LifeOS connects approved sources through plugins, sends every record through one
durable ingest path, turns noisy evidence into reviewable Markdown proposals,
and exposes canonical knowledge to humans and agents through GBrain, the
read-only LifeOS Intelligence Kernel, CLI, and MCP.

Markdown is canon. GBrain and pgGraph are derived. Agents may prepare staging
work. Only an explicit owner promotion writes canon. Capture plugins cannot send
or post as the user.

## What exists in this repository

- A versioned `lifeos.connector/v1` plugin SDK discovered through package entry
  points. Third-party connectors do not fork core.
- Durable SQLite ingest with idempotency, checkpoints, leases, retries, dead
  letters, raw evidence, and source-scoped purge.
- Auto-wiki staging for people and organizations, interaction-based enrichment
  triggers, editable diffs, stale-write rejection, atomic canonical writes, and
  promotion receipts.
- The exact canonical folder model: dashboards, inbox, staging, entities, work,
  knowledge, execution, raw, and archive.
- A GBrain adapter. LifeOS deliberately does not substitute another search
  engine when GBrain is absent.
- A derived pgGraph command seam, never a source of truth.
- A compact, read-only LifeOS Intelligence Kernel `turn_context` packet with
  digest-based `not_modified` behavior.
- CLI, local webhook receiver, and a real MCP v2 server. The default MCP profile
  has no canonical promotion or outbound action tool.
- Optional Screenpipe desktop audio/video capture through its local API. No
  desktop daemon is required for headless installations.

## Honest connector status

| Connector | Status | What is implemented |
| --- | --- | --- |
| Example | Working | Synthetic conformance path |
| Manual note | Working | Local capture into the common ingest path |
| Markdown folder | Working | Backfill, incremental updates, deletions |
| WhatsApp export | Working | User-created text export import and incremental rescan |
| Screenpipe | Working adapter | Local health/search API capture; Screenpipe itself is separate |
| Telegram | Experimental | Telethon user authorization, existing-chat backfill, polling sync |
| WhatsApp Business | Experimental | Signed WABA webhooks; no personal-account history claim |
| Gmail | Experimental | OAuth, message backfill, Gmail history sync, deletion events |
| IMAP | Experimental | Read-only UID backfill and incremental sync |
| Google Calendar | Experimental | OAuth backfill, sync tokens, cancelled events |
| WHOOP | Experimental | OAuth and typed cycle/recovery/workout/sleep/body records |
| X | Experimental | Timeline and mention polling within the owner's API access tier |
| Composio | Experimental | Authenticated trigger webhooks; no action execution |

“Experimental” means the provider adapter executes a real protocol shape and is
covered by synthetic contract tests. It does **not** mean the maintainers have
validated every account type, provider tier, history depth, rate limit, or OAuth
configuration. No user count, revenue, or production reliability claim is made.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[mcp,telegram]'

lifeos init ./brain
lifeos --brain ./brain doctor
lifeos --brain ./brain connector list
```

Run the full local vertical slice:

```bash
lifeos --brain ./brain connector connect example
# Copy the returned connection_id:
lifeos --brain ./brain connector backfill <connection_id>
lifeos --brain ./brain staging list
lifeos --brain ./brain staging show <proposal_id>
lifeos --brain ./brain staging promote <proposal_id> --reviewer "$USER"
```

The proposal exists under `02-staging/` before promotion. The canonical person
page does not. Promotion writes the page and a private receipt under
`.lifeos/receipts/`.

## Connect a source

Provider settings and secrets are separate. Secrets are read from a mode `0600`
JSON file and never accepted as command-line values:

```bash
chmod 600 telegram-secret.json
lifeos --brain ./brain connector connect telegram \
  --settings-file telegram-settings.json \
  --secret-file telegram-secret.json \
  --authorize
```

The built-in secret backend is an owner-only local JSON file. It prevents
accidental Git publication and access by other local Unix users when permissions
are correct. It is **not encrypted by LifeOS at rest**. The `SecretStore`
interface is replaceable by an OS keychain or external secret manager.

For webhook connectors:

```bash
lifeos --brain ./brain webhook-serve --host 127.0.0.1 --port 8765
```

The connection command returns the opaque webhook path. Exposure through a TLS
reverse proxy or tunnel is an operator decision; the built-in receiver listens
on loopback by default.

## GBrain, pgGraph, and the Kernel

GBrain remains the retrieval product. Install it separately and place `gbrain`
on `PATH`, then run:

```bash
lifeos --brain ./brain gbrain-sync
lifeos --brain ./brain search "Ada"
lifeos --brain ./brain query "What is unresolved with Ada?"
```

LifeOS imports only canonical directories into GBrain. It does not index
`01-inbox`, `02-staging`, `07-raw`, or `.lifeos` through this adapter.

pgGraph is optional and derived. Configure an audited builder command in
`.lifeos/config.json` under `pggraph.command`. LifeOS never places provider text,
credentials, or raw payloads into its pgGraph seam.

Compact context injection:

```bash
lifeos --brain ./brain context \
  "Prepare a response about Project Atlas" \
  --subject "Project Atlas"
```

The packet is bounded, evidence-bearing, and read-only. Supplying the previous
packet digest may return `not_modified` with no repeated evidence.

## MCP

```bash
lifeos --brain ./brain mcp-serve
```

Default tools:

- `lifeos.search`
- `lifeos.query`
- `lifeos.get_page`
- `lifeos.get_entity`
- `lifeos.context`
- `lifeos.list_proposals`
- `lifeos.get_proposal`
- `lifeos.connector_health`
- `lifeos.system_health`

`--profile staging` adds only `lifeos.capture_note`. Neither profile exposes
promotion, send, post, purchase, credential access, or arbitrary pgGraph calls.

## Containers

The default Compose file keeps MCP and webhook ports bound to host loopback.
Create the host Markdown directory and run the containers with your host UID/GID
so canonical files remain owner-writable:

```bash
mkdir -p brain
LIFEOS_UID="$(id -u)" LIFEOS_GID="$(id -g)" docker compose up --build
```

Inside the container the MCP process binds `0.0.0.0`; Docker publishes it only
on `127.0.0.1:8787`. Do not expose Streamable HTTP publicly without an
authenticated reverse proxy and explicit origin policy. Stdio remains the
preferred local-agent transport.

## Development

```bash
python -m pip install -e '.[dev,telegram]'
pytest
python -m compileall -q src
python scripts/scan_public.py
python -m build
```

Read [`docs/architecture.md`](docs/architecture.md),
[`docs/connector-authoring.md`](docs/connector-authoring.md), and
[`docs/promotion-model.md`](docs/promotion-model.md) before changing authority
boundaries.

## Current limits

This is an alpha engineering spine, not a finished consumer desktop app. It has
no hosted account, native installer, mobile client, multi-user tenancy, or
published signing trust root. Live-provider certification, backup/restore UX,
release signing, native updates, and an owner review UI remain open work. These
limits are not disguised as “coming soon” capabilities.

## License

MIT. See [`LICENSE`](LICENSE).
