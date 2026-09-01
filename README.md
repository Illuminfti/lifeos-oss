# LifeOS

Open-source capture-to-canon world model.

Connect sources. Ingest once. Review-gated wiki. Query through GBrain, the
LifeOS Intelligence Kernel, MCP, and CLI.

This repository is the public product. It does not contain anyone's private
wiki, credentials, agent souls, or operator estate.

## What it is

1. **Capture plugins** — Telegram, WhatsApp (Business + export), Gmail, IMAP,
   Composio, WHOOP, X, Screenpipe (desktop A/V), Markdown folders, Google Calendar.
2. **One ingest queue** — durable events. No LLM in the queue.
3. **Auto-wiki** — noise to staging proposals. Owner promotes canon.
4. **Retrieval** — GBrain index + Kernel/pgGraph as derived layers. Markdown remains canon.
5. **Agent access** — MCP + CLI + optional tiny context packet.

Connectors are plugins. Core never hard-codes a provider. Empty provider
folders are not allowed. Every named connector is a registered package.

## Status

v0.1.0-alpha. Spine is live: contracts, ingest, wiki tree, CLI, MCP, and
every named connector as a real package. Connect fails closed without
credentials. Fixtures and health work. Live provider clients are the next
implementation pass, all of them, not a one-connector slice.

Desktop A/V is a Screenpipe integration, not a custom capture daemon.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e . pytest
lifeos init ./brain
lifeos connector-list
lifeos doctor
lifeos mcp-serve
```

No secrets in this repo. Connectors store credentials in a local secret
handle you create, never in Markdown.

## License

MIT.
