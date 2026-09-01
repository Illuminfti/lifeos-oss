# Security and privacy

Report vulnerabilities privately to the repository owner.

LifeOS processes unusually sensitive personal evidence. Treat the following as secrets or private instance data:

- connector credentials and refresh tokens,
- provider session databases,
- raw messages, recordings, screenshots, transcripts, and attachments,
- private canonical pages and operational SQLite databases,
- unredacted migration reports and plans,
- graph or index exports derived from a private brain.

The public repository must contain only code, schemas, documentation, and synthetic fixtures. `.lifeos/` is operational state and is ignored in initialized brains. Revoking a connector deletes its credential handle; purging raw evidence must not silently rewrite canonical facts. Canon changes require explicit owner promotion and remain auditable.
