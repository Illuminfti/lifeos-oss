# Architecture

## Authority flow

```text
connector plugin
    │  CaptureEvent v1
    ▼
durable ingest queue ───────► immutable raw evidence
    │
    ▼
normalization and extraction
    │
    ▼
02-staging proposal + editable canonical diff
    │  explicit owner promotion
    ▼
canonical Markdown
    ├────────► GBrain retrieval index
    └────────► pgGraph derived projection
                    │
                    ▼
          LifeOS Intelligence Kernel
                    │
               CLI and MCP
```

## Non-negotiable boundaries

1. Canonical Markdown wins over every derived representation.
2. A capture connector emits events. It cannot edit canon, call pgGraph, or send
   as the owner.
3. The queue is deterministic infrastructure. It does not invoke a model or
   decide truth.
4. Model output is a proposal, never evidence.
5. Promotion is explicit, revision-checked, and receipt-bearing.
6. GBrain is the retrieval layer. LifeOS does not invent a fallback search
   product when it is absent.
7. pgGraph stores only a sanitized, rebuildable projection.
8. The LifeOS Intelligence Kernel is read-only. The Action Plane is disconnected.
9. MCP read access cannot become an implicit promotion or outbound channel.
10. Secrets, provider sessions, cursors, leases, and receipts remain below
    `.lifeos/`, outside canon and retrieval.

## Units

The unit of capture is `CaptureEvent v1`.

The unit of review is one staging proposal with exact evidence references and a
canonical revision.

The unit of release is one LifeOS package version plus its connector protocol,
state schema, Markdown schema, and MCP contract.

The unit of agent context is one bounded Kernel packet, not a wiki dump.
