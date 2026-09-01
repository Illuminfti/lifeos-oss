from __future__ import annotations

from pathlib import Path

import pytest

from lifeos.connectors.base import ConnectorRegistry
from lifeos.connectors.example import ExampleConnector
from lifeos.connectors.markdown_folder import MarkdownFolderConnector
from lifeos.connectors.note import NoteConnector
from lifeos.connectors.whatsapp_export import WhatsAppExportConnector
from lifeos.wiki import init_brain


@pytest.fixture
def brain(tmp_path: Path):
    return init_brain(tmp_path / "brain")


@pytest.fixture
def local_registry():
    return ConnectorRegistry.from_connectors(
        {
            "example": ExampleConnector(),
            "note": NoteConnector(),
            "markdown-folder": MarkdownFolderConnector(),
            "whatsapp-export": WhatsAppExportConnector(),
        }
    )
