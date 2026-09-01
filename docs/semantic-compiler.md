# Semantic compiler

The compiler converts evidence into proposed state changes. It never writes canonical Markdown.

## 1. Evidence validation

The core validates and stores the complete event envelope. A replay of the same scoped provider revision and content is idempotent. Reuse of the same revision for different content is a hard error rather than silent corruption.

Created, updated, deleted, and revoked records form a lineage through `supersedes_event_id`.

## 2. Conservative noise disposition

The deterministic filter handles exact low-information acknowledgements, empty records, and explicitly marked automated notifications. Uncertain material remains evidence-only. “Trash” means no semantic action, not necessarily physical deletion.

## 3. Typed extraction

Extractors emit:

- mentions and identifiers,
- speech acts and modality,
- atomic candidate claims,
- candidate events and decisions,
- candidate open loops,
- numeric or structured observations,
- exact evidence references.

They do not emit wiki prose.

The bundled metadata extractor is a reference contract for synthetic fixtures, connectors, and future local or agent-backed extractors. It does not pretend a proprietary model is included.

## 4. Modality

LifeOS distinguishes:

| Expression | Representation |
|---|---|
| “We did X” | candidate event or actual claim |
| “We decided X” | candidate decision and possible commitments |
| “We should X” | suggestion or possible open loop, not a decision |
| “We might X” | hypothetical evidence |
| “Please send X by Friday” | request/open-loop proposal |
| “I used to work at X” | historical relation with valid time |
| “Someone said X” | attributed assertion |
| calendar booking | scheduled event, not proof of attendance |

## 5. Confidence vector

There is no universal truth score. Every proposal carries separate confidence dimensions:

- extraction,
- identity,
- evidence strength and independence,
- temporal interpretation,
- modality interpretation.

Source authority is predicate-dependent. A calendar is authoritative that something was scheduled, not that it occurred. A wearable is authoritative about its measurement, not the cause. A statement is authoritative that it was said, not automatically that its external content is true.

## 6. Reduction

The claim fingerprint includes subject, predicate, normalized object, polarity, modality, and valid-time scope. Evidence and timestamps are excluded from the semantic identity of the change.

Therefore:

- repeated evidence updates one claim candidate,
- copied or forwarded evidence can share one causal origin,
- one review operation accumulates supporting event IDs,
- append-only prose duplication disappears.

## 7. Temporal truth and contradictions

Valid time and recorded time are separate.

- Same value and overlapping validity: add evidence.
- Different values and non-overlapping validity: history.
- Exclusive different values with overlapping validity: conflict packet.
- Explicit correction: superseding claim.
- Deleted or revoked sole evidence: source-revocation review, not silent deletion.

The predicate registry controls whether multiple simultaneous values are allowed.

## 8. Activity-weighted enrichment

Interaction volume is allowed to affect refresh priority but not truth or intrinsic importance. A production scheduler should use a capped function of owner importance, staleness, change probability, actionability, source diversity, and `log1p(independent evidence)`. This prevents noisy groups from eclipsing quiet but important relationships or life functions.

## 9. Extending extraction

A new extractor must:

1. implement the `Extractor` protocol,
2. emit `SemanticFrame`, `Mention`, `ProposedClaim`, `Operation`, and observation records,
3. preserve source event IDs,
4. use ontology predicate IDs,
5. express uncertainty rather than forcing a subject or modality,
6. pass the synthetic conformance suite.
