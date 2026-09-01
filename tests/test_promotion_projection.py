from pathlib import Path

import pytest

from lifeos.canon import CanonicalVault
from lifeos.evidence import EvidenceStore
from lifeos.ids import new_id
from lifeos.projection import ProjectionBuilder, ProjectionReader
from lifeos.promote import PromotionRequiresOwner, PromotionService
from lifeos.semantic import ConfidenceVector, Operation, ProposedClaim, ReviewPacket, SpawnEvidence, utc_now
from lifeos.wiki import init_brain


def packet_with_person_org(store: EvidenceStore) -> tuple[ReviewPacket, str, str, str, str]:
    person_candidate = store.create_candidate(
        proposed_type="person",
        proposed_kind=None,
        display_name="Synthetic Person",
        spawn_evidence=SpawnEvidence(
            "person",
            display_name="Synthetic Person",
            stable_identifier_count=1,
            meaningful_interaction=True,
        ).to_dict(),
        spawn_policy_version="spawn/v2",
    )
    org_candidate = store.create_candidate(
        proposed_type="organization",
        proposed_kind="business",
        display_name="Synthetic Org",
        spawn_evidence=SpawnEvidence(
            "organization",
            proposed_kind="business",
            display_name="Synthetic Org",
            stable_identifier_count=1,
            durable_relations=1,
        ).to_dict(),
        spawn_policy_version="spawn/v2",
    )
    person_id = new_id("per")
    org_id = new_id("org")
    spawn_person = Operation.create(
        kind="spawn_subject",
        subject_id=person_candidate,
        payload={
            "candidate_id": person_candidate,
            "suggested_subject_id": person_id,
            "type": "person",
            "kind": None,
            "title": "Synthetic Person",
            "spawn_evidence": {},
            "reason": "synthetic test",
            "score": 0.95,
        },
        evidence_ids=["evd_synthetic_1"],
        priority=0.95,
    )
    spawn_org = Operation.create(
        kind="spawn_subject",
        subject_id=org_candidate,
        payload={
            "candidate_id": org_candidate,
            "suggested_subject_id": org_id,
            "type": "organization",
            "kind": "business",
            "title": "Synthetic Org",
            "spawn_evidence": {},
            "reason": "synthetic test",
            "score": 0.95,
        },
        evidence_ids=["evd_synthetic_1"],
        priority=0.95,
    )
    proposal = ProposedClaim.create(
        subject_id=person_candidate,
        subject_type="person",
        predicate="works_at",
        object={"ref": org_candidate, "type": "organization"},
        qualifiers={"role": "advisor", "valid_from": "2026-09-01"},
        evidence_ids=["evd_synthetic_1", "evd_synthetic_2"],
        confidence=ConfidenceVector(0.98, 0.99, 0.85, 0.8, 0.99),
    )
    add_relation = Operation.create(
        kind="add_claim",
        subject_id=person_candidate,
        payload={"claim": proposal.to_dict()},
        evidence_ids=proposal.evidence_ids,
        priority=0.8,
    )
    now = utc_now()
    packet = ReviewPacket(
        packet_id=new_id("pkt"),
        packet_kind="identity_spawn",
        subject_id=person_candidate,
        priority=0.95,
        state="open",
        operations=[spawn_person, spawn_org, add_relation],
        created_at=now,
        updated_at=now,
        summary="Synthetic multi-subject promotion",
        source_event_count=2,
    )
    return store.upsert_review_packet(packet), person_candidate, org_candidate, person_id, org_id


def test_owner_gate_atomic_promotion_and_rebuildable_graph(tmp_path: Path):
    brain = init_brain(tmp_path / "brain")
    store = EvidenceStore(brain / ".lifeos" / "evidence.sqlite")
    packet, person_candidate, org_candidate, person_id, org_id = packet_with_person_org(store)
    vault = CanonicalVault(brain)
    service = PromotionService(vault, store)

    with pytest.raises(PromotionRequiresOwner):
        service.promote_packet(packet.packet_id, actor="agent", owner_confirmed=False)

    transaction_id = service.promote_packet(
        packet.packet_id, actor="owner", owner_confirmed=True
    )
    journal = brain / ".lifeos" / "transactions" / transaction_id / "journal.json"
    assert journal.is_file()
    assert '"state": "committed"' in journal.read_text()

    person = vault.load(person_id)
    org = vault.load(org_id)
    assert person is not None and org is not None
    assert "confidence" not in person.frontmatter  # no page-level truth score
    assert person.frontmatter["claims"][0]["predicate"] == "works_at"
    assert person.frontmatter["claims"][0]["object"]["ref"] == org_id
    assert person.frontmatter["claims"][0]["evidence"] == [
        "evd_synthetic_1",
        "evd_synthetic_2",
    ]
    assert person.frontmatter["claims"][0]["review"]["state"] == "owner_promoted"
    assert store.get_candidate(person_candidate)["promoted_subject_id"] == person_id
    assert store.get_candidate(org_candidate)["promoted_subject_id"] == org_id

    builder = ProjectionBuilder(vault)
    first = builder.rebuild()
    second = builder.rebuild()
    assert first["canon_revision_hash"] == second["canon_revision_hash"]
    reader = ProjectionReader(builder.index_path)
    claims = reader.get_claims(person_id)
    assert claims[0]["object_ref"] == org_id
    graph_path = tmp_path / "graph.jsonl"
    stats = builder.export_graph_jsonl(graph_path)
    assert stats == {"nodes": 2, "edges": 1}
    graph_text = graph_path.read_text()
    assert '"namespace":"canon"' in graph_text
    assert person_id in graph_text and org_id in graph_text


def test_canonical_merge_creates_reversible_redirect_not_deletion(tmp_path: Path):
    brain = init_brain(tmp_path / "brain")
    store = EvidenceStore(brain / ".lifeos" / "evidence.sqlite")
    first_candidate = store.create_candidate(
        proposed_type="person",
        proposed_kind=None,
        display_name="Alex One",
        spawn_evidence=SpawnEvidence("person").to_dict(),
        spawn_policy_version="spawn/v2",
    )
    second_candidate = store.create_candidate(
        proposed_type="person",
        proposed_kind=None,
        display_name="Alex Two",
        spawn_evidence=SpawnEvidence("person").to_dict(),
        spawn_policy_version="spawn/v2",
    )
    first_id, second_id = new_id("per"), new_id("per")
    operations = [
        Operation.create(
            kind="spawn_subject",
            subject_id=first_candidate,
            payload={
                "candidate_id": first_candidate,
                "suggested_subject_id": first_id,
                "type": "person",
                "kind": None,
                "title": "Alex One",
            },
        ),
        Operation.create(
            kind="spawn_subject",
            subject_id=second_candidate,
            payload={
                "candidate_id": second_candidate,
                "suggested_subject_id": second_id,
                "type": "person",
                "kind": None,
                "title": "Alex Two",
            },
        ),
        Operation.create(
            kind="merge_subjects",
            subject_id=first_candidate,
            payload={"source_id": first_candidate, "target_id": second_candidate},
        ),
    ]
    now = utc_now()
    packet = ReviewPacket(
        packet_id=new_id("pkt"),
        packet_kind="identity_spawn",
        subject_id=first_candidate,
        priority=1.0,
        state="open",
        operations=operations,
        created_at=now,
        updated_at=now,
    )
    store.upsert_review_packet(packet)
    vault = CanonicalVault(brain)
    PromotionService(vault, store).promote_packet(
        packet.packet_id, actor="owner", owner_confirmed=True
    )
    source = vault.load(first_id)
    target = vault.load(second_id)
    assert source is not None and target is not None
    assert source.frontmatter["status"] == "merged"
    assert source.frontmatter["redirect_to"] == second_id
    assert source.path.exists()
    assert "Alex One" in target.frontmatter["aliases"]
