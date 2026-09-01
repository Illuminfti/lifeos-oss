from pathlib import Path

from lifeos.cli import main
from lifeos.connectors import REGISTRY
from lifeos.connectors.base import load, load_all
from lifeos.ingest import IngestQueue
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


def test_all_named_connectors_register():
    assert EXPECTED <= set(REGISTRY)
    for cid in EXPECTED:
        plug = load(cid)
        man = plug.describe()
        assert man.id.startswith("org.lifeos.")
        assert man.outbound_actions is False
        health = plug.health()
        assert health.state in {"disconnected", "auth_required", "healthy"}
        if cid != "example":
            result = plug.connect({})
            assert result["ok"] is False


def test_connect_fails_closed_without_secrets():
    for cid in REGISTRY:
        if cid == "example":
            continue
        assert load(cid).connect({})["ok"] is False


def test_ingest_idempotent(tmp_path: Path):
    plug = load("example")
    ev = plug.backfill({})[0]
    q = IngestQueue(tmp_path / "state.sqlite")
    assert q.accept(ev) is True
    assert q.accept(ev) is False
    assert q.count() == 1


def test_brain_tree(tmp_path: Path):
    root = init_brain(tmp_path / "brain")
    assert (root / "CANON.md").is_file()
    assert (root / "03-entities" / "people").is_dir()
    assert (root / "02-staging" / "entities").is_dir()
    assert (root / ".lifeos").is_dir()


def test_cli_doctor(tmp_path: Path, capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "connectors=12" in out
    assert main(["init", str(tmp_path / "b")]) == 0
    assert main(["connector-list"]) == 0
    assert main(["mcp-serve"]) == 0
