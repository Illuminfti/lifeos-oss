# Threat model

## Protected assets

- connector credentials and provider sessions
- raw messages, media metadata, health records, and activity data
- canonical facts and identity links
- promotion authority
- outbound identity and accounts

## Primary threats and controls

| Threat | Control |
| --- | --- |
| Malicious or malformed provider payload | Versioned event validation, bounded HTTP bodies, quarantine through queue failure state |
| Duplicate delivery | Stable event identity and unique source revision key |
| Worker crash | Durable leases, expiry, retry, dead letter |
| Silent canonical overwrite | Staging-only model writes, revision check, explicit owner command, receipts |
| Secret leakage | Separate secret payload, mode `0600` file, no argv secrets, no Markdown/log storage |
| Webhook forgery | Per-connector HMAC or bearer verification before persistence |
| Agent overreach | Curated MCP profiles, no promotion or outbound tool |
| Source revocation | Credential deletion, source-scoped purge, canonical review instead of silent erase |
| Stale or absent retrieval | Explicit GBrain unavailable state; no substitute search product |
| Desktop surveillance without consent | Screenpipe is optional and independently permissioned; LifeOS only reads its configured API |

## Known gaps

The built-in secret backend is not encrypted by LifeOS. The webhook server does
not terminate TLS. Native installer signing, update rollback, encrypted backup,
restore exercises, live-provider certification, and a hardened multi-user server
remain outside this alpha.
