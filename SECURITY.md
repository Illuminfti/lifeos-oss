# Security policy

Do not open a public issue containing credentials, provider sessions, private
captures, or canonical personal data. Report vulnerabilities privately through
GitHub's security advisory flow.

Capture connectors must never expose outbound actions. Secrets must not appear
in command-line arguments, Markdown, logs, fixtures, exceptions, health output,
or MCP responses.

The built-in local secret backend is mode `0600` but not encrypted by LifeOS.
This limitation is part of the public threat model.
