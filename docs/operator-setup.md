# Operator setup

This alpha is local-first engineering software. It does not include a hosted account, managed OAuth application, public tunnel, provider credentials, or a preconfigured GBrain/pgGraph deployment.

## Base installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
lifeos init ./brain
lifeos --brain ./brain doctor
```

Install optional runtime dependencies only for enabled modules:

```bash
pip install -e '.[telegram]'
pip install -e '.[pggraph]'
```

GBrain is an external dependency. Install it separately and make `gbrain` available on `PATH`, or set `LIFEOS_GBRAIN_BIN` to its executable.

## Secret files

Use a private absolute file path with POSIX mode `0600`:

```bash
install -m 600 /dev/null "$HOME/.config/lifeos/gmail.json"
lifeos --brain ./brain connector connect email-gmail \
  --secret-ref "file:$HOME/.config/lifeos/gmail.json"
```

Environment handles are supported for ephemeral development:

```bash
export LIFEOS_PROVIDER_SECRET='{"access_token":"..."}'
# Pass env:LIFEOS_PROVIDER_SECRET, never the token itself.
```

Do not store secret files inside the brain, repository, Markdown folder source, backups intended for sharing, shell history, or diagnostic bundles.

## Required operator inputs

| Connector | Required private input |
|---|---|
| Telegram | API ID, API hash, owner-authorized StringSession or private session file |
| WhatsApp Business | access token, app secret, verification token, phone-number ID, explicit Graph API version |
| WhatsApp export | owner-selected `.txt` or `.zip` export path |
| Gmail | OAuth token or refresh-token client material |
| IMAP | host, port/SSL choice, username, password or app password |
| Composio | API key, connected-account ID, optional webhook secret, explicit read endpoints |
| WHOOP | OAuth token/refresh material, scopes, optional webhook secret |
| X | user access token, granted read scopes, provider product tier |
| Screenpipe | running Screenpipe localhost API, optional API token, enabled content classes |
| Markdown folder | source directory outside the LifeOS brain |
| Google Calendar | OAuth token or refresh-token client material, selected calendars |

LifeOS can implement and test the client code without those values. It cannot prove live authorization, account history coverage, provider tier access, webhook delivery, or rate-limit behavior without them.

## Webhook receiver

```bash
lifeos --brain ./brain webhook serve --host 127.0.0.1 --port 4789
```

The built-in receiver only binds localhost. Public provider delivery requires an operator-managed HTTPS reverse proxy or tunnel. Terminate TLS, preserve the raw body and signature headers, restrict routes, and do not log request bodies.

Routes:

```text
GET  /webhooks/<connector>   provider challenge
POST /webhooks/<connector>   signed delivery
```

The receiver caps bodies at 10 MiB. Signed payloads first enter the durable webhook inbox. Run connector sync to normalize them:

```bash
lifeos --brain ./brain connector sync whatsapp-business
lifeos --brain ./brain connector sync whoop
lifeos --brain ./brain connector sync composio
```

## Normal operating loop

```bash
lifeos --brain ./brain connector health
lifeos --brain ./brain connector sync email-gmail
lifeos --brain ./brain connector sync google-calendar
lifeos --brain ./brain ingest work --limit 100
lifeos --brain ./brain staging list --status awaiting_review
lifeos --brain ./brain staging show prop_...
lifeos --brain ./brain staging promote prop_... --owner local-owner --confirm
lifeos --brain ./brain doctor
```

Automate those commands with one supervised scheduler. Do not create one unobserved cron per connector.

## pgGraph

Set `LIFEOS_PGGRAPH_DSN` only for a database dedicated to the rebuildable graph projection:

```bash
export LIFEOS_PGGRAPH_DSN='postgresql://...'
lifeos --brain ./brain graph rebuild
```

The graph is disposable. Back up canonical Markdown and `.lifeos/receipts`; do not treat the graph as recovery media.

## Live validation receipt

Before calling a connector live-validated, record:

1. provider identity and granted scopes
2. oldest/newest imported record and known exclusions
3. resumable backfill receipt
4. incremental update receipt
5. edit and deletion behavior
6. expired-token/cursor behavior
7. rate-limit behavior
8. revoke and purge result
9. restart from persisted checkpoint
10. confirmation that no outbound operation is available
