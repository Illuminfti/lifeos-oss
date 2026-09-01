from pathlib import Path

from lifeos.cli import main
from lifeos.connectors import registered_connector_ids
from lifeos.connectors.base import load
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


def test_all_named_connectors_are_real_registered_plugins():
    assert EXPECTED <= set(registered_connector_ids())
    for key in EXPECTED:
        plugin = load(key)
        manifest = plugin.describe()
        assert manifest.id.startswith("org.lifeos.")
        assert manifest.source_classes
        assert manifest.outbound_actions is False
        assert hasattr(plugin, "connect")
        assert hasattr(plugin, "backfill")
        assert hasattr(plugin, "sync")
        assert hasattr(plugin, "health")


def test_connect_fails_closed_without_operator_input():
    for key in EXPECTED - {"example"}:
        receipt = load(key).connect({})
        assert receipt.ok is False, key
        assert receipt.state == "auth_required"


def test_brain_tree_and_authority_markers(tmp_path: Path):
    root = init_brain(tmp_path / "brain")
    assert (root / "CANON.md").is_file()
    assert "GBrain and pgGraph are derived" in (root / "CANON.md").read_text()
    assert (root / "02-staging" / "entities").is_dir()
    assert (root / "03-entities" / "people").is_dir()
    assert (root / "07-raw").is_dir()
    assert (root / ".lifeos" / ".gitignore").is_file()


def test_mcp_has_no_canonical_write_or_outbound_tool():
    names = tool_names()
    assert "lifeos.search" in names
    assert "lifeos.context" in names
    forbidden = {"put_page", "promote", "send", "post", "reply", "purchase", "delete"}
    assert not any(any(word in name for word in forbidden) for name in names)


def test_cli_smoke(tmp_path: Path, capsys):
    brain = tmp_path / "brain"
    assert main(["--brain", str(brain), "init"]) == 0
    assert main(["--brain", str(brain), "connector", "list"]) == 0
    assert main(["--brain", str(brain), "doctor"]) == 0
    assert main(["--brain", str(brain), "mcp", "tools"]) == 0
    output = capsys.readouterr().out
    assert "org.lifeos.telegram" in output
    assert "lifeos.context" in output
