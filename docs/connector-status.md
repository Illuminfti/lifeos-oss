# Connector certification status

A connector may be:

- **working**: deterministic local or synthetic behavior is fully exercised by
  the repository test suite;
- **experimental**: a real provider protocol adapter exists and synthetic
  contract tests pass, but live account matrices are incomplete;
- **scaffold**: manifest only. The current tree intentionally ships no bundled
  scaffold connectors.

Provider certification requires recorded, redacted fixtures plus owner-authorized
sandbox tests for authentication, backfill, incremental sync, rate limiting,
reauthorization, update, deletion, revoke, purge, and restart checkpoints.
