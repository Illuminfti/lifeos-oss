from __future__ import annotations

from pathlib import Path

import pytest

from lifeos.autowiki import AutoWiki, CANONICAL_END, CANONICAL_START
from lifeos.contracts import Actor, CaptureEvent, Connection
from lifeos.errors import StaleProposal
from lifeos.storage import StateStore


def capture(connection_id: str = "con_1", text: str = "Discussed Project Atlas") -> CaptureEvent:
    return CaptureEvent.create(
        connector_id="org.lifeos.example",
        connection_id=connection_id,
        source_record_id="m1",
        source_revision="1",
        kind="message.created",
        occurred_at="2026-09-01T10:00:00Z",
        actors=(Actor(provider_ref="person:ada", display_name="Ada Example"),),
        text=text,
    )


def setup_store(brain):
    store = StateStore(brain.db_path)
    store.put_connection(
        Connection(
            connection_id="con_1",
            connector_id="org.lifeos.example",
            settings={},
        ),
        connector_name="example",
    )
    return store


def test_event_stages_but_does_not_write_canon(brain):
    with setup_store(brain) as store:
        wiki = AutoWiki(brain, store)
        proposals = wiki.process_event(capture())
        assert len(proposals) == 1
        proposal = proposals[0]
        assert (brain.root / proposal.staging_path).is_file()
        assert not (brain.root / proposal.target_path).exists()
        assert (brain.root / "01-inbox/captures/2026-09-01" / f"{capture().event_id}.md").is_file()


def test_owner_can_edit_staging_then_promote_with_receipt(brain):
    with setup_store(brain) as store:
        wiki = AutoWiki(brain, store)
        proposal = wiki.process_event(capture())[0]
        staging = brain.root / proposal.staging_path
        text = staging.read_text()
        start = text.index(CANONICAL_START) + len(CANONICAL_START)
        end = text.index(CANONICAL_END)
        canonical = text[start:end]
        canonical = canonical.replace("No broader claim has been promoted.", "Owner reviewed this person page.")
        staging.write_text(text[:start] + canonical + text[end:])
        receipt = wiki.promote(proposal.proposal_id, reviewer="owner")
        target = brain.root / proposal.target_path
        promoted = target.read_text()
        assert "Owner reviewed" in promoted
        assert ".. Evidence:" not in promoted
        assert receipt.before_revision == "missing"
        assert receipt.after_revision.startswith("sha256:")
        assert not staging.exists()
        assert (brain.receipts_dir / "promotions" / f"{receipt.receipt_id}.json").is_file()
        assert store.list_proposals(status="promoted")[0].proposal_id == proposal.proposal_id


def test_stale_canon_blocks_promotion(brain):
    with setup_store(brain) as store:
        wiki = AutoWiki(brain, store)
        proposal = wiki.process_event(capture())[0]
        target = brain.root / proposal.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\nstatus: canonical\n---\n\n# changed\n")
        with pytest.raises(StaleProposal):
            wiki.promote(proposal.proposal_id, reviewer="owner")
        assert store.get_proposal(proposal.proposal_id)[0].status == "stale"


def test_repeated_interactions_enrich_one_active_proposal(brain):
    with setup_store(brain) as store:
        wiki = AutoWiki(brain, store)
        first = wiki.process_event(capture())[0]
        second_event = CaptureEvent.create(
            connector_id="org.lifeos.example",
            connection_id="con_1",
            source_record_id="m2",
            source_revision="1",
            kind="message.created",
            occurred_at="2026-09-02T10:00:00Z",
            actors=(Actor(provider_ref="person:ada", display_name="Ada Example"),),
            text="Second interaction",
        )
        second = wiki.process_event(second_event)[0]
        assert second.proposal_id == first.proposal_id
        assert len(second.evidence_event_ids) == 2
        _, payload = store.get_proposal(second.proposal_id)
        assert payload["interaction_count"] == 2
