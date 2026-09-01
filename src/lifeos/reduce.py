"""Reduce repeated evidence into state deltas instead of append-only prose."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from lifeos.ontology import Ontology, PredicateSpec
from lifeos.semantic import Operation, ProposedClaim


def _parse_time(value: str | None, *, high: bool) -> datetime:
    if not value:
        return datetime.max if high else datetime.min
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed
    except ValueError:
        return datetime.max if high else datetime.min


def validity_overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_start = _parse_time(a.get("valid_from"), high=False)
    a_end = _parse_time(a.get("valid_to"), high=True)
    b_start = _parse_time(b.get("valid_from"), high=False)
    b_end = _parse_time(b.get("valid_to"), high=True)
    return max(a_start, b_start) <= min(a_end, b_end)


class ClaimReducer:
    VERSION = "reducer/v2"

    def __init__(self, ontology: Ontology | None = None):
        self.ontology = ontology or Ontology.default()

    def validate(self, claim: ProposedClaim) -> PredicateSpec:
        object_type = claim.object_type
        if claim.object.get("state") in {"unknown", "no_value"}:
            object_type = self.ontology.predicates[claim.predicate].range[0]
        return self.ontology.validate_claim(
            predicate_id=claim.predicate,
            subject_type=claim.subject_type,
            object_kind=claim.object_kind,
            object_type=object_type,
            qualifiers=claim.qualifiers,
        )

    def reduce(
        self, claim: ProposedClaim, existing_claims: list[dict[str, Any]] | None = None
    ) -> Operation:
        spec = self.validate(claim)
        existing_claims = existing_claims or []
        fingerprint = claim.fingerprint()
        for current in existing_claims:
            if current.get("fingerprint") == fingerprint:
                return Operation.create(
                    kind="attach_evidence",
                    subject_id=claim.subject_id,
                    payload={
                        "claim_id": current["id"],
                        "evidence_ids": claim.evidence_ids,
                        "confidence": claim.confidence.to_dict(),
                    },
                    safe=True,
                    priority=0.25,
                    evidence_ids=claim.evidence_ids,
                )

        if claim.supersedes:
            return Operation.create(
                kind="supersede_claim",
                subject_id=claim.subject_id,
                payload={
                    "supersedes_claim_id": claim.supersedes,
                    "claim": claim.to_dict(),
                },
                safe=False,
                priority=0.85,
                evidence_ids=claim.evidence_ids,
            )

        for current in existing_claims:
            if current.get("predicate") != claim.predicate:
                continue
            if current.get("status", "active") not in {"active", "disputed"}:
                continue
            current_object = current.get("object")
            different_value = current_object != claim.object or current.get("polarity", "positive") != claim.polarity
            exclusive = spec.cardinality == "one" or spec.contradiction_policy == "exclusive_overlap"
            if different_value and exclusive and validity_overlaps(
                current.get("qualifiers", {}), claim.qualifiers
            ):
                return Operation.create(
                    kind="raise_conflict",
                    subject_id=claim.subject_id,
                    payload={
                        "existing_claim_id": current.get("id"),
                        "existing_claim": current,
                        "proposed_claim": claim.to_dict(),
                        "reason": "incompatible values overlap in valid time",
                    },
                    safe=False,
                    priority=0.95,
                    evidence_ids=claim.evidence_ids,
                )

        return Operation.create(
            kind="add_claim",
            subject_id=claim.subject_id,
            payload={"claim": claim.to_dict()},
            safe=False,
            priority=0.55,
            evidence_ids=claim.evidence_ids,
        )
