# Capture connectors

All capture plugins implement `lifeos.connector/v1`, emit `CaptureEvent v1`, persist no literal credential in Markdown, and expose no outbound operation.

## Common lifecycle

```text
connect -> backfill -> sync -> health -> revoke -> purge
```

- **connect** validates configuration and provider identity. Missing credentials fail closed.
- **backfill** reads supported history and returns an explicit completion flag and warnings.
- **sync** resumes from a committed checkpoint. The checkpoint advances only after durable ingest.
- **health** reports provider reachability, authorization, lag/cursor state, and sanitized errors.
- **revoke** stops future capture and removes the local secret reference. Evidence remains until purge.
- **purge** removes source-scoped operational data and derived input. Canon is not silently deleted.

## Secret handles

Connectors accept:

- `env:VARIABLE_NAME`
- `file:/absolute/path/to/secret.json`

Secret files must be mode `0600` or stricter on POSIX. Literal tokens in CLI flags or config JSON are deliberately unsupported.

## Provider matrix

### Telegram

- Auth: Telegram API ID/hash plus an owner-authorized `StringSession` or private session file.
- Backfill: dialogs and messages through Telethon.
- Sync: per-dialog message ID high-water marks with an overlap window for edits.
- Fail closed: missing dependency, API credentials, session, or user authorization.
- Truth boundary: this is a user client, not a Bot API history claim. No send method exists.

### WhatsApp Business

- Auth: Business Platform token, app secret, webhook verification token, phone-number ID, explicit Graph API version.
- Backfill: not claimed. The Business Platform does not expose a general historical inbox export here.
- Sync: signed webhook payloads enter the durable webhook inbox, then normalize into capture events.
- Fail closed: invalid signature, missing token/secret, unavailable phone-number identity.
- Truth boundary: this is not a personal WhatsApp-account scraper.

### WhatsApp export

- Auth: owner-selected local `.txt` or `.zip` export.
- Backfill: parses common exported chat line formats.
- Sync: re-imports changed exports; event hashes suppress duplicates.
- Fail closed: missing, unreadable, or unsupported file.
- Truth boundary: export parsing cannot recover data absent from the export.

### Gmail

- Auth: OAuth access token, or refresh token plus client credentials and token URI.
- Backfill: message-list pagination plus full message fetch.
- Sync: Gmail History ID.
- Fail closed: authorization failure or expired history cursor. An expired cursor raises `FullResyncRequired` rather than pretending sync is current.
- Scope: `gmail.readonly` only.

### IMAP

- Auth: host, username, password/app password, SSL and port configuration.
- Backfill: mailbox `EXAMINE` plus UID search and `BODY.PEEK[]`.
- Sync: UIDVALIDITY and highest UID per folder.
- Fail closed: login, folder, protocol, or network failure.
- Truth boundary: expunge detection is not claimed without server QRESYNC support.

### Composio

- Auth: Composio API key, connected-account ID, optional webhook secret.
- Backfill: only explicitly configured HTTPS GET endpoints.
- Sync: signed trigger webhooks.
- Fail closed: non-GET endpoint, host change, missing account, invalid signature.
- Truth boundary: Composio actions/tool execution are forbidden in capture plugins.

### WHOOP

- Auth: OAuth access token or refresh credentials, optional webhook secret.
- Backfill: configured recovery, cycle, sleep, workout, profile/body resources.
- Sync: provider pagination/high-water timestamps plus signed webhook events.
- Fail closed: missing scopes/token, provider denial, invalid signature.
- Truth boundary: missing measurements remain missing; they are not converted to zero.

### X

- Auth: user access token with granted read scopes.
- Backfill: owned posts, mentions, and optional direct messages where the product tier and scopes permit.
- Sync: provider IDs and pagination cursors.
- Fail closed: identity lookup or core read authorization failure.
- Truth boundary: unavailable streams are reported as warnings. Posting, replying, liking, following, and deleting are absent.

### Screenpipe

- Auth: localhost API and optional API token.
- Backfill: bounded `/search` pagination for enabled OCR, accessibility, audio transcript, input, and metadata classes.
- Sync: timestamp high-water polling.
- Fail closed: unreachable endpoint, unexpected payload, remote endpoint without explicit opt-in, raw-media scope without explicit opt-in.
- Truth boundary: LifeOS does not record, bundle, fork, control, or read Screenpipe's database. Raw frames/audio are not copied by default.

### Markdown folder

- Auth: owner-selected local directory.
- Backfill: recursive `.md` scan.
- Sync: content hashes plus deletion tombstones.
- Fail closed: missing path or any source path that contains/equal the LifeOS brain, preventing ingest loops.

### Google Calendar

- Auth: OAuth access token, or refresh token plus client credentials and token URI.
- Backfill: selected or visible calendars and event pagination.
- Sync: independent `nextSyncToken` per calendar.
- Fail closed: authorization failure or HTTP 410 expired token, which raises `FullResyncRequired`.
- Scope: `calendar.readonly` only.

## Provider validation

CI validates each client against synthetic transports and signed fixtures. A connector is not called live-validated until an operator supplies credentials and records provider-specific receipts for authentication, backfill coverage, incremental sync, expiry/revocation, rate limits, and deletion behavior.
