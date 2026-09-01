"""Actual MCP v2 server for LifeOS agent access."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lifeos import __version__
from lifeos.runtime import LifeOSRuntime
from lifeos.wiki import CANONICAL_PREFIXES, read_page

READ_TOOLS = (
    "lifeos.search",
    "lifeos.query",
    "lifeos.get_page",
    "lifeos.get_entity",
    "lifeos.context",
    "lifeos.list_proposals",
    "lifeos.get_proposal",
    "lifeos.connector_health",
    "lifeos.system_health",
)
STAGING_TOOLS = ("lifeos.capture_note",)


def _safe_read_path(path: str) -> bool:
    normalized = Path(path).as_posix().lstrip("/")
    return normalized in {"CANON.md", "SCHEMA.md"} or any(
        normalized.startswith(prefix) for prefix in CANONICAL_PREFIXES
    )


def create_server(runtime: LifeOSRuntime, *, profile: str = "read") -> Any:
    if profile not in {"read", "staging"}:
        raise ValueError("MCP profile must be read or staging")
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise RuntimeError("MCP support requires `pip install lifeos[mcp]`") from exc

    mcp = MCPServer(
        "LifeOS",
        version=__version__,
        instructions=(
            "Query the owner's review-gated LifeOS brain. Canonical Markdown wins. "
            "GBrain and pgGraph are derived. This server cannot promote canon or execute outbound actions."
        ),
    )

    @mcp.tool(name="lifeos.search")
    def lifeos_search(query: str, limit: int = 10) -> Any:
        """Search canonical LifeOS knowledge through GBrain."""
        return runtime.gbrain.search(query, limit=max(1, min(limit, 50)))

    @mcp.tool(name="lifeos.query")
    def lifeos_query(question: str, limit: int = 10) -> Any:
        """Ask a cited question of canonical LifeOS knowledge through GBrain."""
        return runtime.gbrain.query(question, limit=max(1, min(limit, 50)))

    @mcp.tool(name="lifeos.get_page")
    def lifeos_get_page(path: str) -> dict[str, Any]:
        """Read one canonical Markdown page by relative path."""
        if not _safe_read_path(path):
            raise ValueError("default MCP may read canonical pages only")
        content = read_page(runtime.config, path)
        return {"path": Path(path).as_posix(), "content": content}

    @mcp.tool(name="lifeos.get_entity")
    def lifeos_get_entity(entity_id: str) -> dict[str, Any]:
        """Resolve one exact canonical entity id or page slug."""
        candidate = entity_id.strip()
        for path in (runtime.config.root / "03-entities").rglob("*.md"):
            content = path.read_text(encoding="utf-8")
            if f"id: \"{candidate}\"" in content or f"id: {candidate}" in content or path.stem == candidate:
                return {
                    "path": path.relative_to(runtime.config.root).as_posix(),
                    "content": content,
                }
        return {"found": False, "entity_id": candidate}

    @mcp.tool(name="lifeos.context")
    def lifeos_context(
        purpose: str,
        subjects: list[str] | None = None,
        known_digest: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded read-only LifeOS Intelligence Kernel context packet."""
        packet = runtime.kernel.turn_context(
            purpose=purpose,
            subjects=tuple(subjects or ()),
            known_digest=known_digest,
        )
        return packet.to_dict()

    @mcp.tool(name="lifeos.list_proposals")
    def lifeos_list_proposals(status: str = "awaiting_review", limit: int = 50) -> list[dict[str, Any]]:
        """List review proposals. This tool cannot approve them."""
        requested = None if status == "all" else status
        return [
            proposal.to_dict()
            for proposal in runtime.store.list_proposals(
                status=requested, limit=max(1, min(limit, 200))
            )
        ]

    @mcp.tool(name="lifeos.get_proposal")
    def lifeos_get_proposal(proposal_id: str) -> dict[str, Any]:
        """Read one proposal, its target revision, and evidence references."""
        proposal, payload = runtime.store.get_proposal(proposal_id)
        safe_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"provider_ref_private"}
        }
        return {"proposal": proposal.to_dict(), "payload": safe_payload}

    @mcp.tool(name="lifeos.connector_health")
    def lifeos_connector_health(connection_id: str) -> dict[str, Any]:
        """Read one connector's health without exposing credentials."""
        return runtime.health(connection_id).to_dict()

    @mcp.tool(name="lifeos.system_health")
    def lifeos_system_health() -> dict[str, Any]:
        """Read queue, connector, GBrain, pgGraph, and Kernel health."""
        return dict(runtime.doctor())

    if profile == "staging":
        @mcp.tool(name="lifeos.capture_note")
        def lifeos_capture_note(text: str, title: str = "Agent note") -> dict[str, Any]:
            """Capture a note into the single ingest path. It remains non-canonical."""
            from lifeos.contracts import CaptureEvent

            existing = next(
                (connection for name, connection in runtime.store.list_connections() if name == "note"),
                None,
            )
            connection = existing or runtime.connect("note", {})
            connection_id = connection.connection_id
            event = CaptureEvent.create(
                connector_id="org.lifeos.note",
                connection_id=connection_id,
                source_record_id="note:" + __import__("uuid").uuid4().hex,
                kind="note.captured",
                occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                text=f"{title}\n\n{text}".strip(),
                metadata={"source": "mcp", "title": title},
            )
            from lifeos.contracts import SyncBatch

            receipt = runtime.ingest.accept_batch(connection_id, "capture", SyncBatch(events=(event,), checkpoint={}))
            processed = runtime.process(limit=1)
            return {"event_id": event.event_id, "ingest": receipt.to_dict(), "processed": dict(processed), "canonical": False}

    return mcp


def run_mcp(
    runtime: LifeOSRuntime,
    *,
    profile: str = "read",
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8787,
) -> None:
    server = create_server(runtime, profile=profile)
    if transport == "stdio":
        server.run()
    elif transport in {"streamable-http", "streamable_http"}:
        server.run(transport="streamable-http", host=host, port=port)
    else:
        raise ValueError("transport must be stdio or streamable-http")
