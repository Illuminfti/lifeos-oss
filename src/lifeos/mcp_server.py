"""Read-only MCP-shaped tool dispatch.

Canonical promotion is deliberately absent. A protocol adapter can expose this
registry through an MCP transport without changing the authorization boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lifeos.canon import CanonicalVault
from lifeos.evidence import EvidenceStore
from lifeos.projection import ProjectionReader

TOOLS = [
    "lifeos.search",
    "lifeos.query",
    "lifeos.get_page",
    "lifeos.get_entity",
    "lifeos.context",
    "lifeos.list_review_packets",
    "lifeos.get_review_packet",
    "lifeos.sources",
    "lifeos.connector_health",
    "lifeos.system_health",
]


def tool_names() -> list[str]:
    return list(TOOLS)


class LifeOSTools:
    def __init__(self, brain: Path):
        self.brain = Path(brain).resolve()
        self.vault = CanonicalVault(self.brain)
        self.store = EvidenceStore(self.brain / ".lifeos" / "evidence.sqlite")
        self.index_path = self.brain / ".lifeos" / "index.sqlite"

    def dispatch(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name not in TOOLS:
            raise KeyError(name)
        handler_name = name.replace("lifeos.", "").replace("-", "_")
        handler: Callable[..., Any] = getattr(self, handler_name)
        return handler(**(arguments or {}))

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return ProjectionReader(self.index_path).search(query, limit=limit)

    def query(self, subject_id: str) -> list[dict[str, Any]]:
        return ProjectionReader(self.index_path).get_claims(subject_id)

    def get_page(self, subject_id: str) -> dict[str, Any] | None:
        page = self.vault.load(subject_id)
        if page is None:
            return None
        return {
            "frontmatter": page.frontmatter,
            "body": page.body,
            "path": page.path.relative_to(self.brain).as_posix(),
        }

    def get_entity(self, subject_id: str) -> dict[str, Any] | None:
        return self.get_page(subject_id)

    def context(self, subject_ids: list[str]) -> list[dict[str, Any]]:
        return [value for subject_id in subject_ids if (value := self.get_page(subject_id))]

    def list_review_packets(self, limit: int = 12) -> list[dict[str, Any]]:
        return [packet.to_dict() for packet in self.store.list_review_packets(limit=limit)]

    def get_review_packet(self, packet_id: str) -> dict[str, Any] | None:
        packet = self.store.get_review_packet(packet_id)
        return packet.to_dict() if packet else None

    def sources(self, claim_id: str) -> list[str]:
        if not self.index_path.exists():
            return []
        import sqlite3

        with sqlite3.connect(self.index_path) as conn:
            rows = conn.execute(
                "SELECT evidence_id FROM canon_claim_evidence WHERE claim_id = ? ORDER BY evidence_id",
                (claim_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def connector_health(self) -> dict[str, str]:
        return {"state": "use connector plugin health() for live provider state"}

    def system_health(self) -> dict[str, Any]:
        return {
            "evidence": self.store.stats(),
            "canon_revision_hash": self.vault.revision_hash(),
            "projection_exists": self.index_path.exists(),
            "promotion_tool_exposed": False,
        }


def serve(brain: Path | str = "./brain") -> str:
    return "lifeos read-only mcp tools: " + ", ".join(TOOLS) + f"; brain={Path(brain)}"
