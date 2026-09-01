"""Access the versioned JSON contracts distributed with LifeOS."""
from __future__ import annotations

from importlib.resources import files
import json
from typing import Any

SCHEMAS = (
    "connector-manifest.v1.json",
    "capture-event.v1.json",
    "proposal.v1.json",
    "context-packet.v1.json",
)


def load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMAS:
        raise KeyError(f"unknown LifeOS schema: {name}")
    resource = files("lifeos.schemas").joinpath(name)
    return json.loads(resource.read_text(encoding="utf-8"))
