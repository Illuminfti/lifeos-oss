# Connector authoring

A connector is a separately installable Python package that registers an entry
point in the `lifeos.connectors` group. Core contains no provider registry or
provider-name switch.

```toml
[project.entry-points."lifeos.connectors"]
my-source = "my_lifeos_connector:MyConnector"
```

Implement the `Connector` protocol from `lifeos.connectors.base`:

- `manifest`
- `connect`
- `backfill`
- `sync`
- `health`
- `revoke`
- `purge`
- `test_fixture`

Webhook connectors may additionally implement `verify_webhook` and
`webhook_challenge`.

## Rules

- Emit only `CaptureEvent v1`.
- Use stable provider record IDs and explicit source revisions.
- Advance a checkpoint only through the returned `SyncBatch`. Core commits it
  after every event is durable.
- Report partial coverage and warnings. Never label a truncated backfill
  complete.
- Keep provider schemas inside the plugin. Put only provider-neutral fields in
  the event envelope.
- Return secret material only in `ConnectResult.secret_payload`. Core stores it
  through `SecretStore`; it must not appear in settings, events, Markdown, logs,
  health, or exception messages.
- Declare `outbound_actions = false`. Sending belongs to a separate,
  approval-gated Action Plane.
- `revoke` stops future collection and removes credentials. Existing evidence
  remains until an explicit purge.
- `purge` may perform provider cleanup, but core owns local source-scoped purge.
  Canon is reviewed, never silently erased.

Start from `lifeos.connectors.example` and run the full test suite. A new plugin
must not require a core patch.
