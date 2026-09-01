"""Typed intermediate representation between evidence and canon."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from lifeos.ids import new_id


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalize_literal(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return [normalize_literal(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_literal(value[key]) for key in sorted(value)}
    return value


@dataclass(slots=True)
class ConfidenceVector:
    extraction: float = 0.5
    identity: float = 0.5
    evidence: float = 0.5
    temporal: float = 0.5
    modality: float = 0.5

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"confidence {name} must be between 0 and 1")

    def to_dict(self) -> dict[str, float]:
        return {name: float(value) for name, value in asdict(self).items()}

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ConfidenceVector":
        return cls(**{key: float(val) for key, val in (value or {}).items()})


@dataclass(slots=True)
class IdentifierEvidence:
    namespace: str
    value: str
    confidence: float = 1.0
    scoped_to_connection: bool = True

    def value_hash(self) -> str:
        normalized = self.value.strip().casefold()
        return sha256(normalized.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Mention:
    mention_id: str
    event_id: str
    surface_text: str
    proposed_type: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    identifiers: list[IdentifierEvidence] = field(default_factory=list)
    role: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        surface_text: str,
        proposed_type: str | None = None,
        identifiers: list[IdentifierEvidence] | None = None,
        role: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> "Mention":
        return cls(
            mention_id=new_id("men"),
            event_id=event_id,
            surface_text=surface_text,
            proposed_type=proposed_type,
            identifiers=identifiers or [],
            role=role,
            context=context or {},
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value


@dataclass(slots=True)
class SpawnEvidence:
    proposed_type: str
    proposed_kind: str | None = None
    display_name: str = ""
    stable_identifier_count: int = 0
    independent_evidence_count: int = 0
    interaction_clusters: int = 0
    distinct_days: int = 0
    participant_links: int = 0
    organization_links: int = 0
    context_count: int = 0
    durable_relations: int = 0
    recurring_obligation_days: int = 0
    owner_requested: bool = False
    meaningful_interaction: bool = False
    bounded_outcome: bool = False
    has_owner_or_participant: bool = False
    has_deliverable_or_deadline: bool = False
    changes_state: bool = False
    creates_or_closes_commitment: bool = False
    likely_referenced_later: bool = False
    explicit_decision_language: bool = False
    explicit_request_or_promise: bool = False
    durable_owner_relevance: bool = False
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProposedClaim:
    proposed_claim_id: str
    subject_id: str
    subject_type: str
    predicate: str
    object: dict[str, Any]
    qualifiers: dict[str, Any] = field(default_factory=dict)
    polarity: str = "positive"
    modality: str = "actual"
    confidence: ConfidenceVector = field(default_factory=ConfidenceVector)
    evidence_ids: list[str] = field(default_factory=list)
    asserted_at: str | None = None
    recorded_at: str = field(default_factory=utc_now)
    supersedes: str | None = None
    sensitivity: str = "personal"

    @classmethod
    def create(
        cls,
        *,
        subject_id: str,
        subject_type: str,
        predicate: str,
        object: dict[str, Any],
        evidence_ids: list[str],
        qualifiers: dict[str, Any] | None = None,
        polarity: str = "positive",
        modality: str = "actual",
        confidence: ConfidenceVector | None = None,
        asserted_at: str | None = None,
        supersedes: str | None = None,
        sensitivity: str = "personal",
    ) -> "ProposedClaim":
        return cls(
            proposed_claim_id=new_id("pcl"),
            subject_id=subject_id,
            subject_type=subject_type,
            predicate=predicate,
            object=object,
            qualifiers=qualifiers or {},
            polarity=polarity,
            modality=modality,
            confidence=confidence or ConfidenceVector(),
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            asserted_at=asserted_at,
            supersedes=supersedes,
            sensitivity=sensitivity,
        )

    @property
    def object_kind(self) -> str:
        if "ref" in self.object:
            return "entity_ref"
        if "value" in self.object:
            return "literal"
        if self.object.get("state") in {"unknown", "no_value"}:
            return "literal"
        raise ValueError("claim object must contain ref, value, or state")

    @property
    def object_type(self) -> str | None:
        if self.object_kind == "entity_ref":
            return self.object.get("type")
        return self.object.get("datatype")

    def fingerprint(self) -> str:
        validity = {
            key: self.qualifiers.get(key)
            for key in ("valid_from", "valid_to")
            if key in self.qualifiers
        }
        normalized = {
            "subject_id": self.subject_id,
            "predicate": self.predicate,
            "object": normalize_literal(self.object),
            "polarity": self.polarity,
            "modality": self.modality,
            "validity": normalize_literal(validity),
        }
        return sha256(canonical_json(normalized).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed_claim_id": self.proposed_claim_id,
            "subject_id": self.subject_id,
            "subject_type": self.subject_type,
            "predicate": self.predicate,
            "object": self.object,
            "qualifiers": self.qualifiers,
            "polarity": self.polarity,
            "modality": self.modality,
            "confidence": self.confidence.to_dict(),
            "evidence_ids": list(self.evidence_ids),
            "asserted_at": self.asserted_at,
            "recorded_at": self.recorded_at,
            "supersedes": self.supersedes,
            "sensitivity": self.sensitivity,
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProposedClaim":
        return cls(
            proposed_claim_id=value.get("proposed_claim_id") or new_id("pcl"),
            subject_id=value["subject_id"],
            subject_type=value["subject_type"],
            predicate=value["predicate"],
            object=dict(value["object"]),
            qualifiers=dict(value.get("qualifiers", {})),
            polarity=value.get("polarity", "positive"),
            modality=value.get("modality", "actual"),
            confidence=ConfidenceVector.from_dict(value.get("confidence")),
            evidence_ids=list(value.get("evidence_ids", [])),
            asserted_at=value.get("asserted_at"),
            recorded_at=value.get("recorded_at") or utc_now(),
            supersedes=value.get("supersedes"),
            sensitivity=value.get("sensitivity", "personal"),
        )


@dataclass(slots=True)
class Operation:
    operation_id: str
    kind: str
    subject_id: str | None
    payload: dict[str, Any]
    safe: bool = False
    priority: float = 0.5
    evidence_ids: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        subject_id: str | None,
        payload: dict[str, Any],
        safe: bool = False,
        priority: float = 0.5,
        evidence_ids: list[str] | None = None,
    ) -> "Operation":
        return cls(
            operation_id=new_id("op"),
            kind=kind,
            subject_id=subject_id,
            payload=payload,
            safe=safe,
            priority=priority,
            evidence_ids=list(dict.fromkeys(evidence_ids or [])),
        )

    def fingerprint(self) -> str:
        # Review deduplication keys semantic work, not source-shaped payloads.
        # Evidence and timestamps may grow while the requested state change is
        # still the same operation.
        if self.payload.get("proposed_claim_id"):
            identity: Any = {"proposed_claim_id": self.payload["proposed_claim_id"]}
        elif self.kind in {"add_claim", "supersede_claim"} and self.payload.get("claim"):
            claim = self.payload["claim"]
            identity = {
                "predicate": claim.get("predicate"),
                "object": normalize_literal(claim.get("object")),
                "qualifiers": normalize_literal(claim.get("qualifiers", {})),
                "polarity": claim.get("polarity", "positive"),
                "modality": claim.get("modality", "actual"),
                "supersedes": claim.get("supersedes"),
            }
        elif self.kind == "spawn_subject" and self.payload.get("candidate_id"):
            identity = {"candidate_id": self.payload["candidate_id"]}
        elif self.kind == "attach_evidence" and self.payload.get("claim_id"):
            identity = {"claim_id": self.payload["claim_id"]}
        else:
            identity = normalize_literal(self.payload)
        material = {
            "kind": self.kind,
            "subject_id": self.subject_id,
            "identity": identity,
        }
        return sha256(canonical_json(material).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"fingerprint": self.fingerprint()}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Operation":
        return cls(
            operation_id=value.get("operation_id") or new_id("op"),
            kind=value["kind"],
            subject_id=value.get("subject_id"),
            payload=dict(value.get("payload", {})),
            safe=bool(value.get("safe", False)),
            priority=float(value.get("priority", 0.5)),
            evidence_ids=list(value.get("evidence_ids", [])),
        )


@dataclass(slots=True)
class ReviewPacket:
    packet_id: str
    packet_kind: str
    subject_id: str | None
    priority: float
    state: str
    operations: list[Operation]
    created_at: str
    updated_at: str
    expected_review_seconds: int = 20
    summary: str = ""
    source_event_count: int = 0
    ignored_event_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "packet_kind": self.packet_kind,
            "subject_id": self.subject_id,
            "priority": self.priority,
            "state": self.state,
            "operations": [operation.to_dict() for operation in self.operations],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expected_review_seconds": self.expected_review_seconds,
            "summary": self.summary,
            "source_event_count": self.source_event_count,
            "ignored_event_count": self.ignored_event_count,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewPacket":
        return cls(
            packet_id=value["packet_id"],
            packet_kind=value["packet_kind"],
            subject_id=value.get("subject_id"),
            priority=float(value.get("priority", 0.5)),
            state=value.get("state", "open"),
            operations=[Operation.from_dict(item) for item in value.get("operations", [])],
            created_at=value["created_at"],
            updated_at=value["updated_at"],
            expected_review_seconds=int(value.get("expected_review_seconds", 20)),
            summary=value.get("summary", ""),
            source_event_count=int(value.get("source_event_count", 0)),
            ignored_event_count=int(value.get("ignored_event_count", 0)),
        )


@dataclass(slots=True)
class SemanticFrame:
    event_id: str
    disposition: str = "evidence_only"
    mentions: list[Mention] = field(default_factory=list)
    claims: list[ProposedClaim] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    extractor_version: str = "none"
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class InsightRecord:
    insight_id: str
    algorithm: str
    generated_at: str
    canon_revision_hash: str
    title: str
    body: str
    confidence: float
    input_claim_ids: list[str] = field(default_factory=list)
    input_observation_ids: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    dimensions: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        algorithm: str,
        canon_revision_hash: str,
        title: str,
        body: str,
        confidence: float,
        input_claim_ids: list[str] | None = None,
        input_observation_ids: list[str] | None = None,
        limitations: list[str] | None = None,
        dimensions: dict[str, Any] | None = None,
    ) -> "InsightRecord":
        return cls(
            insight_id=new_id("ins"),
            algorithm=algorithm,
            generated_at=utc_now(),
            canon_revision_hash=canon_revision_hash,
            title=title,
            body=body,
            confidence=max(0.0, min(1.0, float(confidence))),
            input_claim_ids=list(dict.fromkeys(input_claim_ids or [])),
            input_observation_ids=list(dict.fromkeys(input_observation_ids or [])),
            limitations=limitations or [],
            dimensions=dimensions or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
