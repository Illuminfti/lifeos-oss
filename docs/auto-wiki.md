# Auto-wiki and canonical promotion

## States

```text
provider noise
  -> immutable raw evidence
  -> normalized capture event
  -> staging proposal
  -> explicit owner review
  -> canonical Markdown
  -> GBrain sync and pgGraph rebuild
```

Raw evidence and staging are not canon.

## Raw evidence

Each accepted event is preserved under:

```text
07-raw/<connector>/<YYYY>/<MM>/<DD>/<event-id>.json
```

The raw manifest contains provider-neutral event data, hashes, actor hints, and source references. Credentials and provider sessions never enter the manifest.

## Proposals

Auto-wiki creates typed state under `.lifeos/proposals/` and a readable mirror under `02-staging/`. A proposal records:

- target canonical ID and path
- target revision observed when drafted
- source event IDs and content hashes
- proposed compiled truth and aliases
- model and prompt metadata when a model participated
- interaction count and source-class diversity
- conflicts and review state

Repeated interactions update the same awaiting-review entity proposal when the provider identity hint matches.

## Model boundary

The optional model callback receives a bounded event excerpt and actor hint. It may return only:

- `summary`
- `aliases`
- model metadata

Any other output field is rejected. Model output remains a proposal and never becomes evidence.

## Owner promotion

Promotion requires:

- an explicit owner identity
- `confirm=true`
- a proposal in `awaiting_review`
- an unchanged target revision

LifeOS then:

1. renders the owner-edited canonical page
2. writes it atomically
3. records before/after revisions and content
4. records source event IDs and evidence hashes
5. marks the proposal promoted
6. emits a reversible receipt
7. asks GBrain and pgGraph to rebuild their derived state

A failed GBrain or pgGraph rebuild is reported as degraded. It does not roll back a valid canonical write or pretend the index is current.

## Conflict handling

When a canonical target changed after proposal creation, LifeOS marks the proposal `conflict` and refuses promotion. The owner must inspect the new canonical revision and generate or edit a fresh proposal.

## Reversal

A promotion can be reversed from its receipt only while the current target revision still equals the receipt's `after_revision`. This prevents a reversal from erasing later owner edits.

## Enrichment triggers

The default helper marks a proposal due for enrichment when either:

- it has at least five captured interactions, or
- it has at least three interactions from at least two connector classes in the last 30 days

Volume decides when to revisit a page. It does not establish truth or independent corroboration.
