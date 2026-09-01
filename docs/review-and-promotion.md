# Review and owner promotion

## Review unit

LifeOS presents semantic **subject delta packets**, not one item per raw capture. A packet can combine safe evidence attachments, identity questions, spawn proposals, open loops, and suppressed low-information observations. Raw evidence remains collapsed unless opened.

## Four queues

1. `urgent_commitment`: deadlines, promises, requests, waiting states, and blocked work.
2. `conflict`: incompatible claims, corrections, revocations, and unsupported consequential facts.
3. `identity_spawn`: new candidates, ambiguous identity resolution, and merge proposals.
4. `routine_delta`: bundled enrichment for existing subjects.

Further evidence updates an open packet instead of creating another review item.

## Backpressure

As the routine backlog grows, the threshold for new routine packets rises. Low-priority proposals remain in operational state and can accumulate stronger evidence. Urgent commitments and conflicts are never suppressed by routine backlog. The default public policy is twelve visible packets per review session and a routine soft backlog limit of fifty.

## Canonical write boundary

Agents may prepare proposed operations. Only an explicit owner action may apply them to canonical Markdown. The MCP registry deliberately contains no canonical write or promotion tool. The CLI requires an explicit actor and confirmation flag.

## Transaction semantics

A promotion transaction records its ID, source packet, owner actor, accepted operations, expected page revisions, destination paths, before and after hashes, and prepared or committed state. Pages are validated before application. Final contents are staged in a recovery journal and moved into place per file. A prepared transaction can be recovered after interruption.

Supported operations include spawning a subject, adding a claim or relation, attaching evidence, superseding a claim, creating an event or decision, creating or updating an open loop, resolving a conflict, and merging canonical subjects.

An unresolved conflict needs a selected resolution such as keeping the existing claim, accepting the proposed claim, or preserving both as disputed.

## Reversible merges

A canonical merge does not delete the source page. The source becomes:

```yaml
status: merged
redirect_to: per_...
merged_at: ...
```

The target receives unique claims and aliases. Original IDs and the transaction journal remain available for audit or reversal.
