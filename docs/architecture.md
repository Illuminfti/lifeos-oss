# Architecture

Capture plugins emit CaptureEvent v1 into one ingest queue.
Auto-wiki writes 02-staging only.
Owner promotion writes canon.
GBrain and pgGraph are derived.
MCP/CLI query. Default MCP cannot promote or send.
Desktop A/V is the Screenpipe connector, optional.

Named connectors in v0.1: telegram, whatsapp-business, whatsapp-export,
email-gmail, email-imap, composio, whoop, x, screenpipe, markdown-folder,
google-calendar, example.
