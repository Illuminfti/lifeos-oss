"""Identity resolution with exact scoped identifiers and an explicit abstain state."""
from __future__ import annotations

from dataclasses import dataclass

from lifeos.evidence import EvidenceStore
from lifeos.semantic import Mention, SpawnEvidence


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    state: str
    candidate_id: str | None = None
    canonical_subject_id: str | None = None
    confidence: float = 0.0
    alternatives: tuple[str, ...] = ()
    reason: str = ""


class IdentityResolver:
    VERSION = "identity/v2"

    def __init__(self, store: EvidenceStore):
        self.store = store

    def resolve_or_create(
        self,
        mention: Mention,
        *,
        connection_id: str,
        spawn_evidence: SpawnEvidence | None = None,
    ) -> ResolutionResult:
        exact_matches: set[str] = set()
        for identifier in mention.identifiers:
            scope = connection_id if identifier.scoped_to_connection else "global"
            matches = self.store.find_candidates_by_identifier(
                namespace=identifier.namespace,
                scope=scope,
                value_hash=identifier.value_hash(),
            )
            exact_matches.update(str(match["candidate_id"]) for match in matches)

        if len(exact_matches) == 1:
            candidate_id = next(iter(exact_matches))
            candidate = self.store.get_candidate(candidate_id)
            canonical = candidate.get("promoted_subject_id") if candidate else None
            return ResolutionResult(
                state="resolved_canonical" if canonical else "resolved_candidate",
                candidate_id=candidate_id,
                canonical_subject_id=canonical,
                confidence=0.995,
                reason="stable scoped identifier matched",
            )
        if len(exact_matches) > 1:
            return ResolutionResult(
                state="ambiguous",
                confidence=0.0,
                alternatives=tuple(sorted(exact_matches)),
                reason="one identifier maps to multiple active candidates",
            )

        name_matches = self.store.find_candidates_by_name(
            mention.surface_text, mention.proposed_type
        )
        if name_matches:
            # Names nominate candidates but never establish identity.
            return ResolutionResult(
                state="ambiguous_name",
                confidence=0.0,
                alternatives=tuple(str(match["candidate_id"]) for match in name_matches),
                reason="name-only matches require owner or stronger identifier evidence",
            )

        proposed_type = mention.proposed_type or "concept"
        evidence = spawn_evidence or SpawnEvidence(
            proposed_type=proposed_type,
            display_name=mention.surface_text,
            independent_evidence_count=1,
        )
        candidate_id = self.store.create_candidate(
            proposed_type=proposed_type,
            proposed_kind=evidence.proposed_kind,
            display_name=mention.surface_text,
            spawn_evidence=evidence.to_dict(),
            spawn_policy_version="spawn/v2",
        )
        for identifier in mention.identifiers:
            scope = connection_id if identifier.scoped_to_connection else "global"
            self.store.add_candidate_identifier(
                candidate_id=candidate_id,
                namespace=identifier.namespace,
                scope=scope,
                value_hash=identifier.value_hash(),
                confidence=identifier.confidence,
                event_id=mention.event_id,
            )
        return ResolutionResult(
            state="new_candidate",
            candidate_id=candidate_id,
            confidence=0.5 if mention.identifiers else 0.2,
            reason="new unresolved candidate; no canonical identity inferred",
        )
