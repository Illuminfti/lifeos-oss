# Agent access

LifeOS exposes one curated MCP server and the same product through CLI.

The read profile can search and query GBrain, read exact canonical pages and
entities, request a compact Kernel packet, inspect proposals, and inspect health.
It cannot write canon, read secrets, select arbitrary source classes, query
pgGraph directly, or execute an external action.

The staging profile adds one tool: `lifeos.capture_note`. That note enters the
same durable ingest path and remains non-canonical.

Context injection is purpose-scoped and bounded. The caller supplies a purpose
and optional named subjects. It cannot request a wiki dump. A stable packet
digest supports `not_modified` responses.
