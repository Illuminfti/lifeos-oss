from pathlib import Path

from lifeos.cli import main
from lifeos.connectors import REGISTRY
from lifeos.connectors.base import load
from lifeos.evidence import EvidenceStore
from lifeos.mcp_server import tool_names
from lifeos.wiki import init_brain

EXPECTED = {
    "telegram",
    "whatsapp-business",
    "whatsapp-export",
    "email-gmail",
    "email-imap",
    "composio",
    "whoop",
    "x",
    "screenpipe",
    "markdown-folder",
    "google-calendar",
    "example",
}


def test_all_named_connectors_register_and_fail_closed():
    assert EXPECTED <= set(REGISTRY)
    for cid in EXPECTED:
        plug = load(cid)
        man = plug.describe()
        assert man.id.startswith("org.lifeos.")
        assert man.outbound_actions is False
        assert plug.health().state in {"disconnected", "auth_required", "healthy"}
        if cid != "example":
            assert plug.connect({})["ok"] is False


def test_ingest_compatibility_is_evidence_ledger(tmp_path: Path):
    event = load("example").backfill({})[0]
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    assert store.accept(event) is True
    assert store.accept(event) is False
    assert store.count() == 1
    payload = store.get_event(event.event_id)
    assert payload["metadata"]["synthetic"] is True
    assert payload["schema"] == "lifeos.capture-event/v2"


def test_v2_brain_tree(tmp_path: Path):
    root = init_brain(tmp_path / "brain")
    assert (root / "CANON.md").is_file()
    assert (root / "schema" / "ontology.yaml").is_file()
    assert (root / "03-canon" / "person").is_dir()
    assert (root / "03-canon" / "life-function").is_dir()
    assert (root / "02-staging" / "identity").is_dir()
    assert (root / ".lifeos").is_dir()


def test_cli_doctor_and_read_only_mcp(tmp_path: Path, capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "connectors=12" in out
    assert "ontology_types=11" in out
    assert "promotion_tool_exposed=false" in out
    assert all("promote" not in name for name in tool_names())
    assert main(["init", str(tmp_path / "b")]) == 0
    assert main(["connector-list"]) == 0
    assert main(["mcp-serve", "--brain", str(tmp_path / "b")]) == 0
