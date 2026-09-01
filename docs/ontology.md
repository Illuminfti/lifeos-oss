# Public ontology

LifeOS uses one explicit ontology. A folder is only a traversal index.

## Canonical primitives

### Subject

A durable thing that can accumulate identity, claims, relations, and history.

### Occurrence

A bounded thing that happened or was explicitly decided.

### Commitment

An unresolved obligation, request, promise, question, dependency, or follow-up.

### Claim

An atomic proposition about a subject, occurrence, or commitment. A relation is a claim whose object is another canonical subject.

## Root types

| Type | Root | Representative kinds |
|---|---|---|
| `person` | subject | individual |
| `organization` | subject | business, nonprofit, institution, protocol operator, government |
| `collective` | subject | movement, community, household, working group, team |
| `concept` | subject | idea, thesis, hypothesis, principle, topic |
| `project` | subject | project, campaign, initiative, experiment, workstream |
| `life_function` | subject | health, family, work, home, finances, learning, relationships |
| `asset` | subject | physical, financial, digital, account, IP, tool, media, credential |
| `place` | subject | home, venue, city, office, destination, region |
| `event` | occurrence | meeting, interaction, journey, health episode, milestone, transaction |
| `decision` | occurrence | reversible, irreversible, provisional, superseding |
| `open_loop` | commitment | task, promise, request, waiting-on, question, dependency, follow-up |

## Family

`family` is deliberately not a peer type beside `person`.

- Kinship and care are claims such as `parent_of`, `guardian_of`, `spouse_of`, and `sibling_of`.
- A household may be a `collective.kind: household`.
- The owner's ongoing family responsibility may be a `life_function.kind: family`.

This prevents a person, a household, and an area of responsibility from being collapsed into one vague class.

## Type, role, relation, artifact, and state

- Founder, employee, advisor, parent, friend, and customer are roles carried by relation claims.
- A business is `organization.kind: business`.
- A movement is `collective.kind: movement`.
- A workstream or campaign is normally a project kind.
- A task is an open loop where responsibility falls on the owner.
- A relationship is the set of claims and events connecting subjects, not a subject type.
- Dashboard and generated map are views.
- Playbook and checklist are artifacts.
- Inbox, staging, active, merged, and archived are workflow or lifecycle states.

## Predicate registry

Every predicate declares:

```yaml
works_at:
  domain: [person]
  range: [organization]
  object_kind: entity_ref
  temporal: true
  cardinality: many
  inverse: has_worker
  allowed_qualifiers: [role, valid_from, valid_to]
```

This lets the validator reject nonsense such as an organization being the parent of a person. Inverse relations are derived and are not independently asserted as duplicate truths.

## Spawn lifecycle

```text
mention
→ unresolved candidate
→ accumulated identity/evidence
→ qualified candidate
→ owner-facing spawn proposal
→ canonical subject
```

A mention never directly becomes a page.

### Default qualification rules

- **Person**: a stable scoped identifier plus a meaningful interaction, or repeated independent interaction clusters over time.
- **Organization**: a stable domain/provider/legal identifier plus a durable relation, or recurring owner-relevant evidence.
- **Collective**: a stable label, multiple linked participants/organizations, and recurrence across time.
- **Concept**: explicit owner request, or recurrence across contexts with impact on durable work or reasoning.
- **Project**: a bounded desired outcome plus accountable structure, a deliverable, or a deadline.
- **Life function**: owner-created, or proposed only after sustained obligations fail to fit an existing function.
- **Asset**: stable identity plus ownership, control, dependency, maintenance, value, or deliberate recurring use.
- **Place**: recurring relevance or one high-salience durable relation.
- **Event**: changes state, creates/closes commitments, marks a milestone, or is likely to be referenced later.
- **Decision**: explicit choice, commitment, or rejection language. A suggestion is not a decision.
- **Open loop**: an explicit request, promise, obligation, unanswered question, or dependency.

All qualifications remain review-gated.

## Identity and merge rules

1. Stable scoped identifiers are strongest. Provider IDs are scoped to a connection.
2. Names create candidates, never automatic canonical merges.
3. Ambiguous records retain an abstain state and alternatives.
4. Two canonical pages may not be silently merged.
5. A merge preserves the source page as `status: merged` with `redirect_to`.
6. Claims, aliases, evidence associations, revisions, and the merge transaction are retained.
7. Merges cannot cross root types without an explicit migration or reclassification process.
8. Pairwise similarity is not transitively treated as identity.
