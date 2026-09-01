# Contributing

LifeOS is an open-source capture-to-canon world model. Contributions must preserve its authority and privacy boundaries.

## Set up

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m compileall -q src
python -m pytest -q
python -m build
```

## Non-negotiable boundaries

- Markdown canon is changed only by explicit owner promotion.
- Connectors emit `CaptureEvent v1`; they do not write canon.
- Capture plugins contain no send, post, reply, purchase, booking, or provider mutation path.
- GBrain and pgGraph remain derived.
- The LifeOS Intelligence Kernel remains read-only.
- Default MCP has no promotion, credential, canonical write, or outbound tool.
- Screenpipe remains an external API integration, not a bundled or custom recorder.

## Connector contributions

Prefer a third-party package registered under the `lifeos.connectors` entry-point group. A built-in connector needs a clear maintenance and security case.

Every connector change requires synthetic tests for authentication failure, replay, cursor resume, rate limits or typed provider failure, edits/deletions where supported, revoke, purge, and no-secret output. See `docs/plugin-authoring.md`.

## Public-data hygiene

Do not contribute:

- real messages, emails, health data, screen captures, calendars, or exports
- credentials, session files, cookies, IDs copied from private accounts, or webhook signatures
- private wiki text, personal policies, agent souls, Guardian/Hermes material, VPS paths, or operator logs

Use generated identities and deterministic synthetic fixtures. Scrub Git history before opening a pull request if sensitive data was ever committed locally.

## Pull requests

Keep authority changes, provider-client changes, and presentation changes reviewable. State which behavior was exercised with synthetic transports and which behavior was live-validated by an authorized operator. Never convert an untested provider assumption into a shipping claim.
