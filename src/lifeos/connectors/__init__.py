"""Built-in registry plus the public third-party entry-point slot."""
from __future__ import annotations

from importlib import metadata

BUILTIN_REGISTRY = {
    "telegram": "lifeos.connectors.telegram",
    "whatsapp-business": "lifeos.connectors.whatsapp_business",
    "whatsapp-export": "lifeos.connectors.whatsapp_export",
    "email-gmail": "lifeos.connectors.email_gmail",
    "email-imap": "lifeos.connectors.email_imap",
    "composio": "lifeos.connectors.composio",
    "whoop": "lifeos.connectors.whoop",
    "x": "lifeos.connectors.x",
    "screenpipe": "lifeos.connectors.screenpipe",
    "markdown-folder": "lifeos.connectors.markdown_folder",
    "google-calendar": "lifeos.connectors.google_calendar",
    "example": "lifeos.connectors.example",
}

# Compatibility alias for v0.1 callers.
REGISTRY = BUILTIN_REGISTRY


def external_entrypoints():
    entrypoints = metadata.entry_points()
    selected = entrypoints.select(group="lifeos.connectors") if hasattr(entrypoints, "select") else entrypoints.get("lifeos.connectors", [])
    return {entrypoint.name: entrypoint for entrypoint in selected if entrypoint.name not in BUILTIN_REGISTRY}


def registered_connector_ids() -> list[str]:
    return sorted(set(BUILTIN_REGISTRY) | set(external_entrypoints()))
