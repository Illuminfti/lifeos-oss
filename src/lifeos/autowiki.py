"""Compatibility surface for staging-only auto-wiki behavior.

The v2 implementation is `SemanticCompiler`. This module retains a small,
explicitly typed staging helper for external callers while refusing to invent
canon or append source-shaped blocks to canonical pages.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from lifeos.contracts import CaptureEvent
from lifeos.ids import new_id
from lifeos.ontology import Ontology
from lifeos.semantic import utc_now


def propose_entity(
    brain: Path,
    event: CaptureEvent,
    name: str,
    *,
    proposed_type: str = "concept",
    proposed_kind: str | None = None,
) -> Path:
    ontology = Ontology.default()
    ontology.validate_type(proposed_type, proposed_kind)
    proposal_id = new_id("legacy-proposal")
    dest = Path(brain) / "02-staging" / "identity" / f"{proposal_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "schema": "lifeos.legacy-staging-proposal/v2",
        "proposal_id": proposal_id,
        "status": "awaiting_review",
        "proposed_type": proposed_type,
        "proposed_kind": proposed_kind,
        "display_name": name,
        "source_event_ids": [event.event_id],
        "created_at": utc_now(),
        "canonical": False,
    }
    body = (
        f"# Spawn proposal: {name}\n\n"
        "This compatibility proposal is staging only. The semantic compiler should "
        "normally accumulate identity evidence and produce a bounded review packet.\n"
    )
    dest.write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + body,
        encoding="utf-8",
    )
    return dest
