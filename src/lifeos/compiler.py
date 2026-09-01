"""Evidence-to-claim semantic compiler.

This is the missing middle of LifeOS: evidence becomes mentions, candidates,
claims, conflicts, occurrences, commitments, and bounded review packets. It
never writes canon.
"""
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Any

from lifeos.evidence import EvidenceStore
from lifeos.extract import Extractor, MetadataExtractor, NoiseFilter
from lifeos.ids import new_id
from lifeos.ontology import Ontology
from lifeos.reduce import ClaimReducer
from lifeos.resolve import IdentityResolver
from lifeos.review import Packetizer
from lifeos.semantic import Operation, ProposedClaim, ReviewPacket, SpawnEvidence, canonical_json
from lifeos.spawn import SpawnPolicyRegistry

CanonicalClaimsProvider = Callable[[str], list[dict[str, Any]]]


class SemanticCompiler:
    VERSION = "semantic-compiler/v2"

    def __init__(
        self,
        store: EvidenceStore,
        *,
        extractor: Extractor | None = None,
        ontology: Ontology | None = None,
        canonical_claims_provider: CanonicalClaimsProvider | None = None,
    ):
        self.store = store
        self.ontology = ontology or Ontology.default()
        self.extractor = extractor or MetadataExtractor()
        self.noise_filter = NoiseFilter()
        self.resolver = IdentityResolver(store)
        self.spawn = SpawnPolicyRegistry(self.ontology)
        self.reducer = ClaimReducer(self.ontology)
        self.packetizer = Packetizer(store)
        self.canonical_claims_provider = canonical_claims_provider or (lambda _subject_id: [])

    def enqueue(self, event_id: str) -> bool:
        return self.store.enqueue_job(event_id, "semantic", self.VERSION)

    def compile_event(self, event_id: str) -> list[ReviewPacket]:
        event = self.store.get_event(event_id)
        if event is None:
            raise KeyError(event_id)
        self.enqueue(event_id)
        disposition, reason = self.noise_filter.disposition(event)
        if disposition == "ephemeral":
            output_hash = sha256(f"ephemeral:{reason}".encode()).hexdigest()
            self.store.complete_job(event_id, "semantic", self.VERSION, output_hash=output_hash)
            return []

        frame = self.extractor.extract(event)
        frame.disposition = disposition if not frame.mentions and not frame.claims and not frame.operations else frame.disposition
        operations: list[Operation] = list(frame.operations)
        resolved_local: dict[str, tuple[str, str]] = {}
        candidate_to_suggested: dict[str, str] = {}

        for mention in frame.mentions:
            spawn_dict = dict(mention.context.get("spawn_evidence", {}))
            spawn_dict.setdefault("proposed_type", mention.proposed_type or "concept")
            spawn_dict.setdefault("display_name", mention.surface_text)
            evidence = SpawnEvidence(**spawn_dict)
            result = self.resolver.resolve_or_create(
                mention,
                connection_id=event["connection_id"],
                spawn_evidence=evidence,
            )
            local_id = str(mention.context.get("local_id", mention.mention_id))
            resolved_id = result.canonical_subject_id or result.candidate_id
            if resolved_id:
                resolved_local[local_id] = (resolved_id, evidence.proposed_type)
            self.store.record_mention(
                mention,
                extractor_version=frame.extractor_version,
                candidate_subject_id=result.candidate_id,
                canonical_subject_id=result.canonical_subject_id,
                resolution_state=result.state,
                resolution_confidence=result.confidence,
            )

            if result.state in {"ambiguous", "ambiguous_name"}:
                operations.append(
                    Operation.create(
                        kind="resolve_identity",
                        subject_id=None,
                        payload={
                            "mention_id": mention.mention_id,
                            "surface_text": mention.surface_text,
                            "proposed_type": mention.proposed_type,
                            "alternatives": list(result.alternatives),
                            "reason": result.reason,
                        },
                        priority=0.88,
                        evidence_ids=[event_id],
                    )
                )
                continue

            if result.candidate_id and not result.canonical_subject_id:
                decision = self.spawn.evaluate(evidence)
                candidate = self.store.get_candidate(result.candidate_id) or {}
                prior_spawn = candidate.get("spawn_evidence", {})
                prior_suggested = prior_spawn.get("notes", {}).get("suggested_subject_id")
                if decision.qualifies and not prior_suggested:
                    type_spec = self.ontology.validate_type(
                        evidence.proposed_type, evidence.proposed_kind
                    )
                    suggested_id = new_id(type_spec.id_prefix)
                    evidence.notes["suggested_subject_id"] = suggested_id
                    candidate_to_suggested[result.candidate_id] = suggested_id
                    self.store.update_candidate_evidence(
                        result.candidate_id, evidence.to_dict(), state="proposed"
                    )
                    operations.append(
                        Operation.create(
                            kind="spawn_subject",
                            subject_id=result.candidate_id,
                            payload={
                                "candidate_id": result.candidate_id,
                                "suggested_subject_id": suggested_id,
                                "type": evidence.proposed_type,
                                "kind": evidence.proposed_kind,
                                "title": mention.surface_text,
                                "spawn_evidence": evidence.to_dict(),
                                "reason": decision.reason,
                                "score": decision.score,
                            },
                            priority=max(0.70, decision.score),
                            evidence_ids=[event_id],
                        )
                    )
                else:
                    state = "proposed" if prior_suggested else ("qualified" if decision.qualifies else "unresolved")
                    merged = evidence.to_dict()
                    if prior_suggested:
                        merged.setdefault("notes", {})["suggested_subject_id"] = prior_suggested
                    self.store.update_candidate_evidence(
                        result.candidate_id, merged, state=state
                    )

        for claim in frame.claims:
            self._resolve_claim_references(claim, resolved_local)
            proposed_claim_id, _created = self.store.upsert_proposed_claim(
                claim, causal_origin=event.get("metadata", {}).get("causal_origin")
            )
            existing = self.canonical_claims_provider(claim.subject_id)
            operation = self.reducer.reduce(claim, existing)
            operation.payload.setdefault("proposed_claim_id", proposed_claim_id)
            operations.append(operation)

        for operation in operations:
            self._resolve_operation_references(operation, resolved_local)

        for observation in frame.observations:
            self.store.record_observation(
                metric=observation["metric"],
                value=float(observation["value"]) if observation.get("value") is not None else None,
                value_json=observation.get("value_json"),
                unit=observation.get("unit"),
                observed_at=observation.get("observed_at") or event.get("occurred_at") or event["observed_at"],
                source_event_id=event_id,
                dimensions=dict(observation.get("dimensions", {})),
                extractor_version=frame.extractor_version,
            )

        packets = self.packetizer.packetize(
            operations,
            source_event_count=1,
            ignored_event_count=1 if disposition == "evidence_only" and not operations else 0,
        )
        output = {
            "event_id": event_id,
            "disposition": disposition,
            "operations": [operation.to_dict() for operation in operations],
            "packet_ids": [packet.packet_id for packet in packets],
        }
        self.store.complete_job(
            event_id,
            "semantic",
            self.VERSION,
            output_hash=sha256(canonical_json(output).encode()).hexdigest(),
        )
        return packets

    @staticmethod
    def _resolve_claim_references(
        claim: ProposedClaim, resolved_local: dict[str, tuple[str, str]]
    ) -> None:
        if claim.subject_id.startswith("mention:"):
            local_id = claim.subject_id.split(":", 1)[1]
            if local_id not in resolved_local:
                raise ValueError(f"claim references unresolved mention {local_id!r}")
            claim.subject_id, claim.subject_type = resolved_local[local_id]
        ref = claim.object.get("ref")
        if isinstance(ref, str) and ref.startswith("mention:"):
            local_id = ref.split(":", 1)[1]
            if local_id not in resolved_local:
                raise ValueError(f"claim object references unresolved mention {local_id!r}")
            claim.object["ref"], claim.object["type"] = resolved_local[local_id]

    @staticmethod
    def _resolve_operation_references(
        operation: Operation, resolved_local: dict[str, tuple[str, str]]
    ) -> None:
        if isinstance(operation.subject_id, str) and operation.subject_id.startswith("mention:"):
            local_id = operation.subject_id.split(":", 1)[1]
            if local_id in resolved_local:
                operation.subject_id = resolved_local[local_id][0]
        for key, value in list(operation.payload.items()):
            if isinstance(value, str) and value.startswith("mention:"):
                local_id = value.split(":", 1)[1]
                if local_id in resolved_local:
                    operation.payload[key] = resolved_local[local_id][0]
