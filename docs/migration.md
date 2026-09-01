# Private-vault migration

Migration runs locally against a private vault. The public repository receives code, schemas, synthetic fixtures, and redacted aggregate reports only.

## Read-only scan

```bash
lifeos migrate-scan /local/private/vault --output /local/migration-redacted.json
```

By default the report contains counts, legacy classes, dispositions, issue categories, content hashes, and salted path hashes. It contains no page titles, body text, snippets, identifiers, or source paths.

## Legacy mapping

| Legacy class or folder | v2 treatment |
|---|---|
| people | `person` |
| companies / organizations | `organization` |
| protocols | local classification among organization, collective, concept, or asset |
| tools | usually `asset.kind: tool`; sometimes organization |
| media | asset or evidence-only |
| topics | `concept.kind: topic` |
| workstreams / campaigns | `project` |
| relationships | relation claims, household collective, or life function |
| projects | `project` |
| areas | `life_function` |
| concepts | `concept` |
| theses | `concept.kind: thesis` |
| comparisons / maps / dashboards | artifact or derived view |
| decisions | `decision` |
| logs | event records or evidence collection |
| archive | retain semantic type with archived lifecycle state |

## Migration principles

1. Read and hash originals before changing anything.
2. Assign stable IDs independent of titles and paths.
3. Parse legacy append blocks into candidate claims, not canonical truth.
4. Give clearly structured owner-authored facts stronger migration evidence without bypassing review policy.
5. Detect duplicate candidates locally.
6. Apply owner-approved migration transactions.
7. Retain or move originals into `99-archive/legacy-v1/` until verification succeeds.
8. Rebuild SQLite and graph projections from new Markdown.
9. Verify redirects, claims, evidence references, and unresolved records.
10. Never upload a private migration plan or report with private paths.

The existing source-item review backlog should be fed through the reducer and packetizer. It should not be hand-processed one append block at a time.
