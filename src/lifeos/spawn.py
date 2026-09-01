"""Evidence thresholds for proposing durable subjects and occurrences.

A qualifying candidate is still only a proposal. Agents never promote canon.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from lifeos.ontology import Ontology
from lifeos.semantic import SpawnEvidence


@dataclass(frozen=True, slots=True)
class SpawnDecision:
    qualifies: bool
    score: float
    reason: str
    review_required: bool = True


Rule = Callable[[SpawnEvidence], SpawnDecision]


class SpawnPolicyRegistry:
    VERSION = "spawn/v2"

    def __init__(self, ontology: Ontology | None = None):
        self.ontology = ontology or Ontology.default()
        self._rules: dict[str, Rule] = {
            "person": self._person,
            "organization": self._organization,
            "collective": self._collective,
            "concept": self._concept,
            "project": self._project,
            "life_function": self._life_function,
            "asset": self._asset,
            "place": self._place,
            "event": self._event,
            "decision": self._decision,
            "open_loop": self._open_loop,
        }
        missing = set(self.ontology.spawnable_types) - set(self._rules)
        if missing:
            raise RuntimeError(f"spawn policy missing ontology types: {sorted(missing)}")

    def evaluate(self, evidence: SpawnEvidence) -> SpawnDecision:
        self.ontology.validate_type(evidence.proposed_type, evidence.proposed_kind)
        return self._rules[evidence.proposed_type](evidence)

    @staticmethod
    def _yes(score: float, reason: str) -> SpawnDecision:
        return SpawnDecision(True, min(1.0, score), reason)

    @staticmethod
    def _no(score: float, reason: str) -> SpawnDecision:
        return SpawnDecision(False, max(0.0, score), reason)

    def _person(self, e: SpawnEvidence) -> SpawnDecision:
        if e.stable_identifier_count >= 1 and e.meaningful_interaction:
            return self._yes(0.94, "stable identifier plus meaningful interaction")
        if e.independent_evidence_count >= 3 and e.interaction_clusters >= 3 and e.distinct_days >= 2:
            return self._yes(0.80, "recurring independently observed person")
        return self._no(0.25, "passing or unresolved person mention")

    def _organization(self, e: SpawnEvidence) -> SpawnDecision:
        if e.stable_identifier_count >= 1 and e.durable_relations >= 1:
            return self._yes(0.93, "stable organization identifier plus durable relation")
        if e.independent_evidence_count >= 2 and e.durable_relations >= 1:
            return self._yes(0.76, "recurring organization with owner relevance")
        return self._no(0.25, "product or organization label lacks durable relevance")

    def _collective(self, e: SpawnEvidence) -> SpawnDecision:
        links = e.participant_links + e.organization_links
        if e.display_name and links >= 2 and e.independent_evidence_count >= 2 and e.distinct_days >= 2:
            return self._yes(0.79, "durable named group with multiple linked participants")
        return self._no(0.20, "temporary chat, hashtag, or attendee list")

    def _concept(self, e: SpawnEvidence) -> SpawnDecision:
        if e.owner_requested:
            return self._yes(0.98, "owner explicitly requested concept tracking")
        if e.context_count >= 2 and e.durable_relations >= 1:
            return self._yes(0.73, "idea recurs across contexts and affects durable work")
        return self._no(0.18, "passing noun, quotation, or hypothetical idea")

    def _project(self, e: SpawnEvidence) -> SpawnDecision:
        if e.bounded_outcome and (e.has_owner_or_participant or e.has_deliverable_or_deadline):
            return self._yes(0.91, "bounded outcome with accountable structure")
        return self._no(0.25, "recurring responsibility belongs under a life function")

    def _life_function(self, e: SpawnEvidence) -> SpawnDecision:
        if e.owner_requested:
            return self._yes(0.99, "owner explicitly defined an ongoing responsibility")
        if e.recurring_obligation_days >= 30 and e.independent_evidence_count >= 5:
            return self._yes(0.70, "sustained obligations do not fit an existing function")
        return self._no(0.05, "agents need a very high threshold for new life functions")

    def _asset(self, e: SpawnEvidence) -> SpawnDecision:
        if e.stable_identifier_count >= 1 and (
            e.durable_owner_relevance or e.durable_relations >= 1
        ):
            return self._yes(0.88, "stable resource with ownership, control, dependency, or deliberate use")
        return self._no(0.20, "ordinary attachment, receipt, link, or transient media")

    def _place(self, e: SpawnEvidence) -> SpawnDecision:
        if e.independent_evidence_count >= 2 and e.distinct_days >= 2:
            return self._yes(0.75, "place has recurring relevance")
        if e.durable_relations >= 1 and e.likely_referenced_later:
            return self._yes(0.72, "one high-salience place relation")
        return self._no(0.15, "one-off geocoded mention")

    def _event(self, e: SpawnEvidence) -> SpawnDecision:
        if e.changes_state or e.creates_or_closes_commitment or e.likely_referenced_later:
            return self._yes(0.84, "occurrence changes durable state or will be referenced later")
        return self._no(0.15, "routine telemetry remains a structured observation")

    def _decision(self, e: SpawnEvidence) -> SpawnDecision:
        if e.explicit_decision_language:
            return self._yes(0.90, "explicit choice, commitment, or rejection")
        return self._no(0.10, "suggestion, preference, or hypothetical is not a decision")

    def _open_loop(self, e: SpawnEvidence) -> SpawnDecision:
        if e.explicit_request_or_promise or e.creates_or_closes_commitment:
            return self._yes(0.90, "explicit request, promise, obligation, or dependency")
        return self._no(0.10, "possibility language has no responsible party or expected follow-up")
