from __future__ import annotations

from pathlib import Path


def source_files():
    root = Path(__file__).parents[1]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".pytest_cache", "__pycache__", "dist"} for part in path.parts):
            continue
        if path.suffix in {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".sh"} or path.name in {"Dockerfile", "Makefile"}:
            yield path


def test_public_tree_contains_no_private_estate_markers():
    forbidden = (
        "/home/" + "ubuntu/",
        "illumi" + "-wiki",
        "Guardian" + " soul",
        "Hermes" + " Guardian",
        "BEGIN OPENSSH" + " PRIVATE KEY",
        "BEGIN RSA" + " PRIVATE KEY",
    )
    for path in source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in forbidden:
            assert marker not in text, f"{marker!r} leaked through {path}"


def test_no_capture_connector_declares_outbound_actions():
    from lifeos.connectors.base import ConnectorRegistry

    for registration in ConnectorRegistry.discover().registrations():
        assert registration.connector.manifest.outbound_actions is False
