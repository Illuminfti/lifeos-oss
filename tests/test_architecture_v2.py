from datetime import datetime, timezone
from pathlib import Path

import pytest

from lifeos.compiler import SemanticCompiler
from lifeos.contracts import CaptureEvent
from lifeos.evidence import EvidenceRevisionConflict, EvidenceStore
from lifeos.extract import MetadataExtractor
from lifeos.ontology import Ontology, OntologyError
from lifeos.reduce import ClaimReducer
from lifeos.resolve import IdentityResolver
from lifeos.semantic import (
    ConfidenceVector,
    IdentifierEvidence,
    Mention,
    ProposedClaim,
    SpawnEvidence,
)
from lifeos.spawn import SpawnPolicyRegistry


def event(
    *,
    event_id: str = "evt_1",
    revision: str = "1",
    text: str = "hello",
    metadata: dict | None = None,
    deleted: bool = False,
    supersedes: str | None = None,
) -> CaptureEvent:
    now = "2026-09-01T12:00:00Z"
    return CaptureEvent(
        event_id=event_id,
        brain_id="brain_test",
        connector_id="org.lifeos.example",
        connection_id="con_account_a",
        source_record_id="record_1",
        source_revision=revision,
        kind="message.updated" if revision != "1" else "message.created",
        occurred_at=now,
        observed_at=now,
        text=text,
        metadata=metadata or {"nested": {"preserved": True}},
        actors=[{"role": "sender", "external_id": "person-1"}],
        conversation={"external_id": "thread-1", "kind": "direct"},
        attachments=[{"kind": "image", "raw_ref": "sha256:abc"}],
        links=[{"url": "https://example.invalid"}],
        raw_ref="sha256:def",
        deleted=deleted,
        supersedes_event_id=supersedes,
    )


def test_evidence_preserves_full_envelope_and_revision_chain(tmp_path: Path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    first = event()
    assert store.accept(first)
    payload = store.get_event(first.event_id)
    assert payload["metadata"]["nested"]["preserved"] is True
    assert payload["actors"][0]["external_id"] == "person-1"
    assert payload["conversation"]["external_id"] == "thread-1"
    assert payload["attachments"][0]["raw_ref"] == "sha256:abc"
    assert payload["raw_ref"] == "sha256:def"

    update = event(event_id="evt_2", revision="2", text="corrected", supersedes="evt_1")
    assert store.accept(update)
    assert store.get_event("evt_2")["supersedes_event_id"] == "evt_1"

    tombstone = event(
        event_id="evt_3", revision="3", text="", deleted=True, supersedes="evt_2"
    )
    assert store.accept(tombstone)
    assert store.get_event("evt_3")["deleted"] is True


def test_reused_provider_revision_with_different_content_is_hard_error(tmp_path: Path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    assert store.accept(event(text="one"))
    with pytest.raises(EvidenceRevisionConflict):
        store.accept(event(event_id="evt_other", text="two"))


def test_processing_jobs_are_leaseable_retryable_and_replay_safe(tmp_path: Path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    store.accept(event())
    assert store.enqueue_job("evt_1", "semantic", "v2")
    assert not store.enqueue_job("evt_1", "semantic", "v2")
    assert store.lease_jobs(
        stage="semantic", processor_version="v2", worker_id="worker-a", limit=10
    ) == ["evt_1"]
    assert store.lease_jobs(
        stage="semantic", processor_version="v2", worker_id="worker-b", limit=10
    ) == []
    assert store.fail_job("evt_1", "semantic", "v2", error="boom") == "pending"
    assert store.lease_jobs(
        stage="semantic", processor_version="v2", worker_id="worker-b", limit=10
    ) == ["evt_1"]
    store.complete_job("evt_1", "semantic", "v2", output_hash="abc")
    assert store.lease_jobs(
        stage="semantic", processor_version="v2", worker_id="worker-c", limit=10
    ) == []


def test_one_ontology_separates_types_roles_relations_and_views():
    ontology = Ontology.default()
    assert set(ontology.types) == {
        "person",
        "organization",
        "collective",
        "concept",
        "project",
        "life_function",
        "asset",
        "place",
        "event",
        "decision",
        "open_loop",
    }
    assert "family" not in ontology.types
    assert "relationship" not in ontology.types
    assert "dashboard" not in ontology.types
    ontology.validate_claim(
        predicate_id="parent_of",
        subject_type="person",
        object_kind="entity_ref",
        object_type="person",
    )
    with pytest.raises(OntologyError):
        ontology.validate_claim(
            predicate_id="parent_of",
            subject_type="organization",
            object_kind="entity_ref",
            object_type="person",
        )


def test_spawn_rules_cover_all_major_types():
    policies = SpawnPolicyRegistry()
    positives = [
        SpawnEvidence("person", display_name="P", stable_identifier_count=1, meaningful_interaction=True),
        SpawnEvidence("organization", display_name="O", stable_identifier_count=1, durable_relations=1),
        SpawnEvidence("collective", display_name="C", participant_links=2, independent_evidence_count=2, distinct_days=2),
        SpawnEvidence("concept", display_name="I", context_count=2, durable_relations=1),
        SpawnEvidence("project", display_name="P", bounded_outcome=True, has_deliverable_or_deadline=True),
        SpawnEvidence("life_function", proposed_kind="custom", display_name="F", owner_requested=True),
        SpawnEvidence("asset", display_name="A", stable_identifier_count=1, durable_owner_relevance=True),
        SpawnEvidence("place", display_name="L", independent_evidence_count=2, distinct_days=2),
        SpawnEvidence("event", proposed_kind="meeting", display_name="E", changes_state=True),
        SpawnEvidence("decision", proposed_kind="reversible", display_name="D", explicit_decision_language=True),
        SpawnEvidence("open_loop", proposed_kind="request", display_name="Q", explicit_request_or_promise=True),
    ]
    assert {value.proposed_type for value in positives} == set(policies.ontology.types)
    assert all(policies.evaluate(value).qualifies for value in positives)
    for type_id in policies.ontology.types:
        kind = policies.ontology.types[type_id].kinds[0] if policies.ontology.types[type_id].kinds else None
        assert not policies.evaluate(SpawnEvidence(type_id, proposed_kind=kind)).qualifies


def test_identity_resolver_never_merges_by_name_alone(tmp_path: Path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    store.accept(event())
    first = store.create_candidate(
        proposed_type="person",
        display_name="Alex Example",
        proposed_kind=None,
        spawn_evidence=SpawnEvidence("person").to_dict(),
        spawn_policy_version="spawn/v2",
    )
    second = store.create_candidate(
        proposed_type="person",
        display_name="Alex Example",
        proposed_kind=None,
        spawn_evidence=SpawnEvidence("person").to_dict(),
        spawn_policy_version="spawn/v2",
    )
    resolver = IdentityResolver(store)
    name_only = Mention.create(
        event_id="evt_1", surface_text="Alex Example", proposed_type="person"
    )
    result = resolver.resolve_or_create(name_only, connection_id="con_account_a")
    assert result.state == "ambiguous_name"
    assert set(result.alternatives) == {first, second}

    identifier = IdentifierEvidence(namespace="provider-user", value="u-1")
    store.add_candidate_identifier(
        candidate_id=first,
        namespace=identifier.namespace,
        scope="con_account_a",
        value_hash=identifier.value_hash(),
        confidence=1.0,
        event_id="evt_1",
    )
    exact = Mention.create(
        event_id="evt_1",
        surface_text="Alex Example",
        proposed_type="person",
        identifiers=[identifier],
    )
    resolved = resolver.resolve_or_create(exact, connection_id="con_account_a")
    assert resolved.state == "resolved_candidate"
    assert resolved.candidate_id == first


def test_claim_reducer_deduplicates_and_raises_temporal_conflicts():
    reducer = ClaimReducer()
    proposal = ProposedClaim.create(
        subject_id="prj_1",
        subject_type="project",
        predicate="status",
        object={"value": "active", "datatype": "string"},
        qualifiers={"valid_from": "2026-09-01"},
        evidence_ids=["evt_1"],
        confidence=ConfidenceVector(0.9, 0.9, 0.8, 0.8, 0.9),
    )
    same = {
        "id": "clm_1",
        "predicate": "status",
        "object": {"value": "active", "datatype": "string"},
        "polarity": "positive",
        "modality": "actual",
        "qualifiers": {"valid_from": "2026-09-01"},
        "status": "active",
        "fingerprint": proposal.fingerprint(),
    }
    assert reducer.reduce(proposal, [same]).kind == "attach_evidence"

    conflicting = dict(same)
    conflicting["fingerprint"] = "different"
    conflicting["object"] = {"value": "paused", "datatype": "string"}
    assert reducer.reduce(proposal, [conflicting]).kind == "raise_conflict"


def semantic_metadata() -> dict:
    return {
        "causal_origin": "origin:synthetic-conversation",
        "semantic": {
            "mentions": [
                {
                    "local_id": "person",
                    "surface_text": "Synthetic Person",
                    "proposed_type": "person",
                    "identifiers": [{"namespace": "user", "value": "p1"}],
                    "spawn_evidence": {
                        "proposed_type": "person",
                        "display_name": "Synthetic Person",
                        "stable_identifier_count": 1,
                        "meaningful_interaction": True,
                    },
                },
                {
                    "local_id": "org",
                    "surface_text": "Synthetic Org",
                    "proposed_type": "organization",
                    "identifiers": [{"namespace": "domain", "value": "synthetic.invalid", "scoped_to_connection": False}],
                    "spawn_evidence": {
                        "proposed_type": "organization",
                        "display_name": "Synthetic Org",
                        "stable_identifier_count": 1,
                        "durable_relations": 1,
                    },
                },
            ],
            "claims": [
                {
                    "subject_id": "mention:person",
                    "subject_type": "person",
                    "predicate": "works_at",
                    "object": {"ref": "mention:org", "type": "organization"},
                    "qualifiers": {"role": "advisor", "valid_from": "2026-09-01"},
                    "confidence": {
                        "extraction": 0.95,
                        "identity": 0.95,
                        "evidence": 0.7,
                        "temporal": 0.7,
                        "modality": 0.95,
                    },
                }
            ],
        },
    }


def test_compiler_turns_events_into_bounded_subject_deltas(tmp_path: Path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    first = event(metadata=semantic_metadata())
    store.accept(first)
    compiler = SemanticCompiler(store, extractor=MetadataExtractor())
    packets = compiler.compile_event(first.event_id)
    assert len(packets) == 3  # two identity/spawn packets and one relation delta
    assert len(store.list_proposed_claims()) == 1

    second = event(
        event_id="evt_2",
        revision="2",
        text="same fact repeated with more evidence",
        metadata=semantic_metadata(),
        supersedes="evt_1",
    )
    store.accept(second)
    compiler.compile_event(second.event_id)
    # Repeated evidence strengthens one proposed claim and existing packets.
    assert len(store.list_proposed_claims()) == 1
    open_packets = store.list_review_packets(limit=100)
    assert len(open_packets) == 3
    routine = [packet for packet in open_packets if packet.packet_kind == "routine_delta"]
    assert len(routine) == 1
    assert len(routine[0].operations) == 1
    proposed = store.list_proposed_claims()[0]
    assert set(proposed.evidence_ids) == {"evt_1", "evt_2"}
