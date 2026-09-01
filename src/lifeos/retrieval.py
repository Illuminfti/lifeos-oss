"""Derived retrieval adapters and the read-only LifeOS Intelligence Kernel.

Markdown is canon. GBrain and pgGraph are rebuildable projections. This module
never promotes staging material and never mutates a provider.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable
from uuid import uuid4

from lifeos.contracts import ContextPacket, stable_hash, utc_now
from lifeos.errors import ConfigurationError, ProviderUnavailable
from lifeos.wiki import canonical_pages, parse_frontmatter, revision, safe_path


class GBrainAdapter:
    """Thin wrapper around the existing GBrain CLI. No fallback search engine."""

    def __init__(
        self,
        brain: Path,
        *,
        executable: str | None = None,
        timeout: float = 120.0,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.brain = Path(brain).resolve()
        self.executable = executable or os.environ.get("LIFEOS_GBRAIN_BIN", "gbrain")
        self.timeout = timeout
        self.environment = dict(environment or {})

    def available(self) -> bool:
        return bool(shutil.which(self.executable))

    def sync(self) -> dict[str, Any]:
        return self._run(["sync", "--repo", str(self.brain), "--json"])

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not tool or not re.fullmatch(r"[a-zA-Z0-9_.-]+", tool):
            raise ConfigurationError("invalid GBrain tool name")
        return self._run(
            [
                "call",
                tool,
                json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                "--repo",
                str(self.brain),
                "--json",
            ]
        )

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        return self.call("search", {"query": query, "limit": int(limit)})

    def query(self, question: str, *, limit: int = 10) -> dict[str, Any]:
        return self.call("query", {"question": question, "limit": int(limit)})

    def _run(self, arguments: list[str]) -> dict[str, Any]:
        if not self.available():
            raise ConfigurationError(
                "GBrain is not installed or LIFEOS_GBRAIN_BIN does not point to an executable"
            )
        environment = os.environ.copy()
        environment.update(self.environment)
        try:
            completed = subprocess.run(
                [self.executable, *arguments],
                cwd=self.brain,
                env=environment,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderUnavailable(f"GBrain invocation failed: {type(exc).__name__}") from exc
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "unknown GBrain failure").strip()
            raise ProviderUnavailable(f"GBrain failed ({completed.returncode}): {message[:1000]}")
        output = completed.stdout.strip()
        if not output:
            return {"ok": True}
        try:
            value = json.loads(output)
        except json.JSONDecodeError:
            return {"ok": True, "text": output}
        return value if isinstance(value, dict) else {"ok": True, "data": value}


@dataclass(slots=True)
class GraphNode:
    id: str
    type: str
    title: str
    path: str
    revision: str


class PgGraphAdapter:
    """Rebuildable metadata-only graph projection.

    pgGraph never stores raw captures, page bodies, account identifiers, or
    credentials. Consumers hydrate selected nodes from canonical Markdown.
    """

    LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")

    def __init__(self, brain: Path, dsn: str | None = None) -> None:
        self.brain = Path(brain).resolve()
        self.dsn = dsn or os.environ.get("LIFEOS_PGGRAPH_DSN")

    def rebuild(self) -> dict[str, Any]:
        nodes, edges = self.project()
        if not self.dsn:
            return {
                "ok": False,
                "state": "not_configured",
                "nodes": len(nodes),
                "edges": len(edges),
                "note": "set LIFEOS_PGGRAPH_DSN to persist the derived projection",
            }
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise ConfigurationError("install lifeos[pggraph] to persist pgGraph") from exc
        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS lifeos_graph_nodes(
                    id TEXT PRIMARY KEY,type TEXT NOT NULL,title TEXT NOT NULL,path TEXT NOT NULL,revision TEXT NOT NULL)"""
                )
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS lifeos_graph_edges(
                    source_id TEXT NOT NULL,target_ref TEXT NOT NULL,kind TEXT NOT NULL,
                    PRIMARY KEY(source_id,target_ref,kind))"""
                )
                cursor.execute("TRUNCATE lifeos_graph_edges, lifeos_graph_nodes")
                cursor.executemany(
                    "INSERT INTO lifeos_graph_nodes(id,type,title,path,revision) VALUES(%s,%s,%s,%s,%s)",
                    [(node.id, node.type, node.title, node.path, node.revision) for node in nodes],
                )
                cursor.executemany(
                    "INSERT INTO lifeos_graph_edges(source_id,target_ref,kind) VALUES(%s,%s,%s)",
                    edges,
                )
            connection.commit()
        return {"ok": True, "state": "healthy", "nodes": len(nodes), "edges": len(edges)}

    def project(self) -> tuple[list[GraphNode], list[tuple[str, str, str]]]:
        nodes: list[GraphNode] = []
        edges: list[tuple[str, str, str]] = []
        for page in canonical_pages(self.brain):
            text = page.read_text(encoding="utf-8", errors="replace")
            frontmatter, _ = parse_frontmatter(text)
            node_id = str(frontmatter.get("id") or page.relative_to(self.brain).as_posix())
            node = GraphNode(
                id=node_id,
                type=str(frontmatter.get("type") or page.parent.name),
                title=str(frontmatter.get("title") or page.stem),
                path=page.relative_to(self.brain).as_posix(),
                revision=revision(page),
            )
            nodes.append(node)
            for target in sorted(set(self.LINK_RE.findall(text))):
                edges.append((node_id, target.strip(), "wiki_link"))
        return nodes, edges

    def neighbours(self, node_id: str, *, limit: int = 25) -> dict[str, Any]:
        if not self.dsn:
            return {"ok": False, "state": "not_configured", "items": []}
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise ConfigurationError("install lifeos[pggraph] to query pgGraph") from exc
        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT source_id,target_ref,kind FROM lifeos_graph_edges
                    WHERE source_id=%s OR target_ref=%s ORDER BY source_id,target_ref LIMIT %s""",
                    (node_id, node_id, int(limit)),
                )
                rows = cursor.fetchall()
        return {
            "ok": True,
            "state": "healthy",
            "items": [
                {"source_id": str(row[0]), "target_ref": str(row[1]), "kind": str(row[2])}
                for row in rows
            ],
        }


class QueryService:
    """Safe read facade used by CLI, MCP, and the Kernel."""

    READABLE_ROOTS = {
        "CANON.md",
        "SCHEMA.md",
        "README.md",
        "00-dashboards",
        "03-entities",
        "04-work",
        "05-knowledge",
        "06-execution",
        "99-archive",
    }

    def __init__(self, brain: Path, *, gbrain: GBrainAdapter | None = None) -> None:
        self.brain = Path(brain).resolve()
        self.gbrain = gbrain or GBrainAdapter(self.brain)

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        return self.gbrain.search(query, limit=limit)

    def query(self, question: str, *, limit: int = 10) -> dict[str, Any]:
        return self.gbrain.query(question, limit=limit)

    def get_page(self, path: str) -> dict[str, Any]:
        normalized = Path(path).as_posix().lstrip("/")
        first = normalized.split("/", 1)[0]
        if first not in self.READABLE_ROOTS:
            raise PermissionError("MCP/CLI page reads are limited to canonical and dashboard paths")
        target = safe_path(self.brain, normalized)
        if not target.is_file() or target.suffix.lower() != ".md":
            raise FileNotFoundError(normalized)
        return {
            "path": normalized,
            "revision": revision(target),
            "content": target.read_text(encoding="utf-8", errors="replace"),
        }

    def get_entity(self, entity: str) -> dict[str, Any]:
        needle = entity.casefold().strip()
        matches: list[dict[str, Any]] = []
        for page in canonical_pages(self.brain):
            if not page.relative_to(self.brain).as_posix().startswith("03-entities/"):
                continue
            text = page.read_text(encoding="utf-8", errors="replace")
            frontmatter, _ = parse_frontmatter(text)
            aliases = [str(value) for value in frontmatter.get("aliases") or []]
            haystack = {
                str(frontmatter.get("id") or "").casefold(),
                str(frontmatter.get("title") or page.stem).casefold(),
                page.stem.casefold(),
                *(alias.casefold() for alias in aliases),
            }
            if needle in haystack:
                matches.append(
                    {
                        "id": frontmatter.get("id"),
                        "title": frontmatter.get("title") or page.stem,
                        "path": page.relative_to(self.brain).as_posix(),
                        "revision": revision(page),
                        "content": text,
                    }
                )
        if not matches:
            raise FileNotFoundError(entity)
        if len(matches) > 1:
            return {"ambiguous": True, "matches": matches}
        return matches[0]


class LifeOSIntelligenceKernel:
    """Compile a tiny, purpose-scoped, read-only context packet."""

    DEFAULT_TOKEN_BUDGET = 800
    HARD_TOKEN_BUDGET = 2000

    def __init__(self, brain: Path, *, query_service: QueryService | None = None) -> None:
        self.brain = Path(brain).resolve()
        self.query_service = query_service or QueryService(self.brain)

    def context(
        self,
        *,
        purpose: str,
        entities: Iterable[str] | None = None,
        previous_digest: str | None = None,
        max_tokens: int = DEFAULT_TOKEN_BUDGET,
    ) -> ContextPacket:
        purpose = purpose.strip()
        if not purpose:
            raise ValueError("purpose is required")
        budget = max(100, min(int(max_tokens), self.HARD_TOKEN_BUDGET))
        named = [str(value).strip() for value in entities or [] if str(value).strip()]
        search_text = " ".join([purpose, *named])
        failed_sources: list[str] = []
        raw: dict[str, Any]
        try:
            raw = self.query_service.search(search_text, limit=12)
        except Exception as exc:
            raw = {"items": []}
            failed_sources.append(f"gbrain:{type(exc).__name__}")
        items = self._items(raw)
        current_facts: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        used = 0
        character_budget = budget * 4
        for item in items:
            excerpt = str(
                item.get("excerpt")
                or item.get("text")
                or item.get("content")
                or item.get("summary")
                or ""
            ).strip()
            path = str(item.get("path") or item.get("file") or item.get("source") or "")
            if not excerpt:
                continue
            allowance = max(0, character_budget - used)
            if allowance <= 0:
                break
            clipped = excerpt[:allowance]
            used += len(clipped)
            reference = f"ev_{len(evidence) + 1}"
            evidence.append(
                {
                    "ref": reference,
                    "path": path or None,
                    "revision": item.get("revision") or item.get("hash"),
                    "score": item.get("score"),
                }
            )
            current_facts.append(
                {
                    "claim": clipped,
                    "confidence": item.get("confidence") or "unknown",
                    "evidence_refs": [reference],
                }
            )
        stable = {
            "purpose": purpose,
            "entities": named,
            "current_facts": current_facts,
            "evidence": evidence,
            "coverage": {
                "current_sources": ["wiki"] if items else [],
                "stale_sources": [],
                "denied_sources": [],
                "failed_sources": failed_sources,
            },
        }
        digest = stable_hash(stable)
        if previous_digest and previous_digest == digest:
            return ContextPacket.unchanged(purpose, digest)
        return ContextPacket(
            packet_id="pkt_" + uuid4().hex,
            purpose=purpose,
            as_of=utc_now(),
            current_facts=current_facts,
            recent_changes=[],
            open_loops=[],
            constraints=[],
            evidence=evidence,
            coverage=stable["coverage"],
            digest=digest,
            status="ok" if not failed_sources else "partial",
        )

    @staticmethod
    def _items(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return []
        for key in ["items", "results", "matches", "data", "pages"]:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
            if isinstance(candidate, dict):
                nested = LifeOSIntelligenceKernel._items(candidate)
                if nested:
                    return nested
        return []
