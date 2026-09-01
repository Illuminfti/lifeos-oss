"""Staging proposals. Never writes canon."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from lifeos.contracts import CaptureEvent


def propose_entity(brain: Path, event: CaptureEvent, name: str) -> Path:
    dest = Path(brain) / "02-staging" / "entities" / f"{uuid4().hex[:8]}-{name}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "\n".join(
            [
                "---",
                "status: awaiting_review",
                f"source_event: {event.event_id}",
                f"connector: {event.connector_id}",
                "---",
                "",
                f"# Proposal: {name}",
                "",
                "This is staging. Not canon.",
                "",
                f"Evidence text: {event.text}",
                "",
            ]
        )
    )
    return dest
