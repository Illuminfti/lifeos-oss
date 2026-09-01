import json
from pathlib import Path

from lifeos.canon import CanonicalVault
from lifeos.contracts import CaptureEvent
from lifeos.evidence import EvidenceStore
from lifeos.ids import new_id
from lifeos.insights import InsightEngine
from lifeos.migration import LegacyVaultScanner, MigrationPlanner
from lifeos.projection import ProjectionBuilder
from lifeos.review import Packetizer
from lifeos.semantic import Operation
from lifeos.wiki import init_brain


def test_migration_reports_are_redacted_by_default(tmp_path: Path):
    private_vault = tmp_path / "private-vault"
    page = private_vault / "03-entities" / "people" / "Secret Person.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntype: entity\nstatus: active\n---\n\n# Secret Person\n\nExtremely private sentence.\n",
        encoding="utf-8",
    )
    report = LegacyVaultScanner(private_vault).redacted_report()
    encoded = json.dumps(report)
    assert report["privacy"]["redacted"] is True
    assert "Secret Person" not in encoded
    assert "Extremely private sentence" not in encoded
    assert str(page) not in encoded
    assert report["counts"]["pages"] == 1
    assert report["pages"][0]["path_hash"]
    plan = MigrationPlanner(LegacyVaultScanner(private_vault)).plan()
    assert "source_path" not in plan["items"][0]


def test_review_packetizer_collapses_repeated_operations(tmp_path: Path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    packetizer = Packetizer(store)
    for index in range(100):
        operation = Operation.create(
            kind="add_claim",
            subject_id="per_1",
            payload={"claim": {"predicate": "description", "object": {"value": "same"}}},
            evidence_ids=[f"evt_{index}"],
            priority=0.6,
        )
        packetizer.packetize([operation], source_event_count=1)
    packets = store.list_review_packets(limit=100)
    assert len(packets) == 1
    assert len(packets[0].operations) == 1
    assert packets[0].source_event_count == 100
    assert len(packets[0].operations[0].evidence_ids) == 100


def test_derived_insights_carry_provenance_and_avoid_causal_claims(tmp_path: Path):
    brain = init_brain(tmp_path / "brain")
    vault = CanonicalVault(brain)
    projection = ProjectionBuilder(vault)
    projection.rebuild()
    store = EvidenceStore(brain / ".lifeos" / "evidence.sqlite")
    for day in range(1, 11):
        date = f"2026-08-{day:02d}T08:00:00Z"
        event_id = f"evt_metric_{day}"
        event = CaptureEvent(
            event_id=event_id,
            brain_id="brain_test",
            connector_id="org.lifeos.whoop",
            connection_id="con_whoop",
            source_record_id=f"day_{day}",
            source_revision="1",
            kind="measurement.created",
            occurred_at=date,
            observed_at=date,
            text="",
            metadata={"synthetic": True},
        )
        store.accept(event)
        store.record_observation(
            metric="sleep_hours",
            value=float(day),
            unit="hours",
            observed_at=date,
            source_event_id=event_id,
        )
        store.record_observation(
            metric="task_switches",
            value=float(11 - day),
            unit="count",
            observed_at=date,
            source_event_id=event_id,
        )
    engine = InsightEngine(projection.index_path, store)
    pattern = engine.self_pattern("sleep_hours", "task_switches")
    assert pattern.input_observation_ids
    assert pattern.dimensions["sample_size"] == 10
    assert pattern.dimensions["pearson_r"] < -0.99
    assert "not a causal claim" in pattern.body
    assert pattern.algorithm == "self-pattern-correlation/v2"

    relationship = engine.relationship_radar("per_missing")
    assert relationship.algorithm == "relationship-radar/v2"
    assert relationship.limitations
    assert engine.life_function_coverage() == []
    assert engine.decision_outcomes() == []
    assert engine.circumstance_changes(days=30).input_claim_ids == []
    assert engine.leverage_map().limitations
