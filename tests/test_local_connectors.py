from pathlib import Path
import zipfile

from lifeos.connectors.base import ConnectorManager
from lifeos.ingest import IngestQueue


def test_markdown_folder_backfill_update_and_delete(brain: Path, tmp_path: Path):
    source = tmp_path / "notes"
    source.mkdir()
    note = source / "alpha.md"
    note.write_text("# Alpha\n\nFirst revision.\n", encoding="utf-8")

    manager = ConnectorManager(brain)
    receipt = manager.connect("markdown-folder", {"config": {"path": str(source)}})
    assert receipt.ok

    first = manager.run("markdown-folder", "backfill")
    assert first["stored"] == 1
    assert manager.queue.count() == 1

    note.write_text("# Alpha\n\nSecond revision.\n", encoding="utf-8")
    second = manager.run("markdown-folder", "sync")
    assert second["stored"] == 1

    note.unlink()
    third = manager.run("markdown-folder", "sync")
    assert third["stored"] == 1
    claimed = manager.queue.claim(limit=10)
    assert any(item.deleted and item.kind == "document.deleted" for item in claimed)


def test_markdown_folder_rejects_the_canonical_brain(brain: Path):
    manager = ConnectorManager(brain)
    receipt = manager.connect("markdown-folder", {"config": {"path": str(brain)}})
    assert receipt.ok is False
    assert "brain" in (receipt.message or "").lower()


def test_whatsapp_text_export_is_real_ingest(brain: Path, tmp_path: Path):
    export = tmp_path / "WhatsApp Chat with Ada.txt"
    export.write_text(
        "01/09/2026, 10:00 - Ada Lovelace: Hello from the export\n"
        "01/09/2026, 10:01 - Owner: Hello Ada\n"
        "continuation line\n",
        encoding="utf-8",
    )
    manager = ConnectorManager(brain)
    receipt = manager.connect("whatsapp-export", {"config": {"path": str(export)}})
    assert receipt.ok
    result = manager.run("whatsapp-export", "backfill")
    assert result["stored"] == 2
    events = manager.queue.claim(limit=10)
    assert {event.actors[0].display_name for event in events} == {"Ada Lovelace", "Owner"}
    assert any("continuation line" in event.text for event in events)


def test_whatsapp_zip_export(brain: Path, tmp_path: Path):
    archive = tmp_path / "chat.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("_chat.txt", "[09/01/2026, 10:00:00] Ada: From zip\n")
    manager = ConnectorManager(brain)
    receipt = manager.connect("whatsapp-export", {"config": {"path": str(archive)}})
    assert receipt.ok
    result = manager.run("whatsapp-export", "backfill")
    assert result["stored"] == 1
