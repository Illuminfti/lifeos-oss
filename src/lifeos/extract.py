"""Typed semantic extraction boundary.

The core never asks a model to write wiki prose. Extractors, including future
agent-backed implementations, must emit this validated intermediate form.
"""
from __future__ import annotations

from typing import Any, Protocol

from lifeos.semantic import (
    ConfidenceVector,
    IdentifierEvidence,
    Mention,
    Operation,
    ProposedClaim,
    SemanticFrame,
)


class Extractor(Protocol):
    version: str

    def extract(self, event: dict[str, Any]) -> SemanticFrame: ...


class NoiseFilter:
    """Conservative deterministic filter. Uncertainty remains evidence-only."""

    VERSION = "noise/v2"
    _ACKS = {
        "ok",
        "okay",
        "k",
        "thanks",
        "thank you",
        "got it",
        "👍",
        "👌",
        "noted",
    }

    def disposition(self, event: dict[str, Any]) -> tuple[str, str]:
        metadata = event.get("metadata", {})
        text = " ".join(str(event.get("text", "")).casefold().strip().split())
        if event.get("deleted"):
            return "evidence_only", "source tombstone may retract support"
        if metadata.get("semantic_disposition") in {
            "ephemeral",
            "evidence_only",
            "interpret",
        }:
            return str(metadata["semantic_disposition"]), "connector supplied disposition hint"
        if metadata.get("automated") and metadata.get("notification"):
            return "ephemeral", "known automated notification"
        if not text and not event.get("attachments") and not metadata.get("semantic"):
            return "ephemeral", "empty event"
        if text in self._ACKS and not event.get("attachments"):
            return "ephemeral", "low-information acknowledgement"
        return "interpret", "potential semantic novelty"


class MetadataExtractor:
    """Reference extractor for tests, adapters, and agent conformance.

    Connectors or a local agent may attach `metadata.semantic`. This extractor
    proves the full path without pretending that the public package ships a
    proprietary model. Unsupported evidence safely remains evidence-only.
    """

    version = "metadata-semantic/v2"

    def extract(self, event: dict[str, Any]) -> SemanticFrame:
        semantic = event.get("metadata", {}).get("semantic") or {}
        frame = SemanticFrame(
            event_id=event["event_id"],
            disposition="interpret" if semantic else "evidence_only",
            extractor_version=self.version,
        )
        for index, item in enumerate(semantic.get("mentions", [])):
            identifiers = [
                IdentifierEvidence(
                    namespace=value["namespace"],
                    value=str(value["value"]),
                    confidence=float(value.get("confidence", 1.0)),
                    scoped_to_connection=bool(value.get("scoped_to_connection", True)),
                )
                for value in item.get("identifiers", [])
            ]
            context = dict(item.get("context", {}))
            context.setdefault("local_id", item.get("local_id", f"mention-{index}"))
            if "spawn_evidence" in item:
                context["spawn_evidence"] = dict(item["spawn_evidence"])
            frame.mentions.append(
                Mention.create(
                    event_id=event["event_id"],
                    surface_text=item["surface_text"],
                    proposed_type=item.get("proposed_type"),
                    identifiers=identifiers,
                    role=item.get("role"),
                    context=context,
                )
            )
        for item in semantic.get("claims", []):
            confidence = ConfidenceVector.from_dict(item.get("confidence"))
            frame.claims.append(
                ProposedClaim.create(
                    subject_id=item["subject_id"],
                    subject_type=item["subject_type"],
                    predicate=item["predicate"],
                    object=dict(item["object"]),
                    qualifiers=dict(item.get("qualifiers", {})),
                    polarity=item.get("polarity", "positive"),
                    modality=item.get("modality", "actual"),
                    confidence=confidence,
                    evidence_ids=[event["event_id"]],
                    asserted_at=item.get("asserted_at") or event.get("occurred_at"),
                    supersedes=item.get("supersedes"),
                    sensitivity=item.get("sensitivity", event.get("sensitivity", "personal")),
                )
            )
        for item in semantic.get("operations", []):
            frame.operations.append(
                Operation.create(
                    kind=item["kind"],
                    subject_id=item.get("subject_id"),
                    payload=dict(item.get("payload", {})),
                    safe=bool(item.get("safe", False)),
                    priority=float(item.get("priority", 0.5)),
                    evidence_ids=[event["event_id"]],
                )
            )
        frame.observations = [dict(item) for item in semantic.get("observations", [])]
        return frame
