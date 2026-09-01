from __future__ import annotations

from pathlib import Path

import pytest

from lifeos.autowiki import AutoWiki, ProposalStore
from lifeos.contracts import CaptureActor, CaptureEvent
from lifeos.errors import PromotionConflict
from lifeos.mcp_server import MCPApplication, handle_message, tool_names
from lifeos.retrieval import LifeOSIntelligenceKernel, QueryService


def person_event(text: str = "Discussed the Atlas launch") -> CaptureEvent:
    return CaptureEvent.build(
        connector_id="org.lifeos.telegram",
        connection_id="con_tg",
        source_record_id="chat-1:message-1",
        source_revision="1",
        source_thread_id="chat-1",
        kind="message.created",
        occurred_at="2026-09-01T12:00:00Z",
        text=text,
        actors=[CaptureActor(display_name="Ada Lovelace", provider_ref="telegram:42", role="sender")],
    )


def test_raw_to_staging_to_owner_promoted_canon_and_reverse(brain: Path):
    wiki = AutoWiki(brain)
    proposals = wiki.process(person_event())
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.status == "awaiting_review"
    assert not (brain / proposal.target_path).exists()
    assert list((brain / "07-raw" / "telegram").rglob("*.json"))
    assert list((brain / "02-staging" / "entities").glob(f"{proposal.proposal_id}-*.md"))

    store = ProposalStore(brain)
    result = store.promote(
        proposal.proposal_id,
        owner="test-owner",
        confirm=True,
        edited_summary="Ada is a person represented in owner-reviewed evidence.",
    )
    target = Path(result["target"])
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "status: \"canonical\"" in text
    assert "Ada is a person represented" in text
    assert proposal.evidence[0]["event_id"] in text

    reversal = store.reverse(result["receipt"], owner="test-owner", confirm=True)
    assert reversal["reverses"].startswith("prm_")
    assert not target.exists()


def test_promotion_refuses_stale_target_revision(brain: Path):
    proposal = AutoWiki(brain).process(person_event())[0]
    target = brain / proposal.target_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("owner changed canon", encoding="utf-8")
    with pytest.raises(PromotionConflict):
        ProposalStore(brain).promote(proposal.proposal_id, owner="owner", confirm=True)
    assert ProposalStore(brain).get(proposal.proposal_id).status == "conflict"


def test_model_is_confined_to_proposal_fields(brain: Path):
    def bad_model(payload):
        return {"summary": "draft", "canonical": True}

    with pytest.raises(ValueError):
        AutoWiki(brain, model=bad_model).process(person_event())
    assert not list((brain / "03-entities").rglob("*.md"))


class FakeQuery:
    def search(self, query: str, limit: int = 10):
        return {
            "items": [
                {
                    "path": "03-entities/people/ada.md",
                    "revision": "rev-1",
                    "excerpt": "Ada is connected to Project Atlas.",
                    "score": 0.9,
                }
            ]
        }

    def query(self, question: str, limit: int = 10):
        return {"answer": "Synthetic answer", "citations": ["03-entities/people/ada.md"]}

    def get_page(self, path: str):
        return {"path": path, "content": "synthetic", "revision": "rev-1"}

    def get_entity(self, entity: str):
        return {"id": "ent_ada", "title": entity, "path": "03-entities/people/ada.md"}


def test_kernel_packet_is_bounded_digestible_and_not_modified(brain: Path):
    query = FakeQuery()
    kernel = LifeOSIntelligenceKernel(brain, query_service=query)  # type: ignore[arg-type]
    packet = kernel.context(purpose="Prepare for Atlas", entities=["Ada"], max_tokens=800)
    assert packet.status == "ok"
    assert packet.current_facts[0]["evidence_refs"] == ["ev_1"]
    assert len(packet.digest) == 64
    unchanged = kernel.context(
        purpose="Prepare for Atlas",
        entities=["Ada"],
        previous_digest=packet.digest,
        max_tokens=800,
    )
    assert unchanged.status == "not_modified"
    assert unchanged.current_facts == []


def test_query_service_blocks_raw_and_state_paths(brain: Path):
    service = QueryService(brain)
    with pytest.raises(PermissionError):
        service.get_page("07-raw/secret.json")
    with pytest.raises(PermissionError):
        service.get_page(".lifeos/state.sqlite")
    with pytest.raises(ValueError):
        service.get_page("03-entities/../../outside.md")


def test_mcp_lists_and_calls_reads_without_write_escape(brain: Path):
    query = FakeQuery()
    kernel = LifeOSIntelligenceKernel(brain, query_service=query)  # type: ignore[arg-type]
    application = MCPApplication(brain, query_service=query, kernel=kernel)  # type: ignore[arg-type]
    listing = handle_message(application, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listing is not None
    names = {tool["name"] for tool in listing["result"]["tools"]}
    assert names == set(tool_names())
    assert "lifeos.promote" not in names

    response = handle_message(
        application,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "lifeos.context", "arguments": {"purpose": "Atlas"}},
        },
    )
    assert response is not None
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["schema"] == "lifeos.turn-context/v1"

    with pytest.raises(KeyError):
        application.call("lifeos.promote", {"proposal_id": "prop_x"})
