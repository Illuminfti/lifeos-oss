"""Curated read-only MCP server for LifeOS.

The MCP surface intentionally has no canonical put/delete, promotion, provider
credential, send, post, reply, purchase, or arbitrary pgGraph tool.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, TextIO

from lifeos.autowiki import ProposalStore
from lifeos.connectors.base import ConnectorManager
from lifeos.retrieval import LifeOSIntelligenceKernel, QueryService
from lifeos.wiki import init_brain

PROTOCOL_VERSION = "2024-11-05"

TOOL_DEFINITIONS = [
    {
        "name": "lifeos.search",
        "description": "Search canonical Markdown through GBrain.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.query",
        "description": "Ask a cited question of canonical Markdown through GBrain.",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.get_page",
        "description": "Read one canonical or dashboard Markdown page by relative path.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.get_entity",
        "description": "Resolve one canonical entity by id, title, alias, or slug.",
        "inputSchema": {
            "type": "object",
            "properties": {"entity": {"type": "string"}},
            "required": ["entity"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.context",
        "description": "Compile a small purpose-scoped read-only LifeOS Intelligence Kernel packet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string"},
                "entities": {"type": "array", "items": {"type": "string"}},
                "previous_digest": {"type": "string"},
                "max_tokens": {"type": "integer", "minimum": 100, "maximum": 2000},
            },
            "required": ["purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.list_proposals",
        "description": "List auto-wiki staging proposals. Staging is not canon.",
        "inputSchema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.get_proposal",
        "description": "Read one staging proposal, its diff inputs, and evidence references.",
        "inputSchema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.sources",
        "description": "Show source coverage for a purpose-scoped Kernel packet.",
        "inputSchema": {
            "type": "object",
            "properties": {"purpose": {"type": "string"}, "entities": {"type": "array", "items": {"type": "string"}}},
            "required": ["purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.connector_health",
        "description": "Read sanitized health for one capture connector.",
        "inputSchema": {
            "type": "object",
            "properties": {"connector": {"type": "string"}},
            "required": ["connector"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lifeos.system_health",
        "description": "Read queue, proposal, connector, GBrain, and canon health without secrets.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def tool_names() -> list[str]:
    return [str(tool["name"]) for tool in TOOL_DEFINITIONS]


class MCPApplication:
    def __init__(
        self,
        brain: Path,
        *,
        query_service: QueryService | None = None,
        kernel: LifeOSIntelligenceKernel | None = None,
        connectors: ConnectorManager | None = None,
    ) -> None:
        self.brain = init_brain(Path(brain))
        self.query_service = query_service or QueryService(self.brain)
        self.kernel = kernel or LifeOSIntelligenceKernel(self.brain, query_service=self.query_service)
        self.connectors = connectors or ConnectorManager(self.brain)
        self.proposals = ProposalStore(self.brain)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = dict(arguments or {})
        if name == "lifeos.search":
            return self.query_service.search(str(arguments["query"]), limit=int(arguments.get("limit", 10)))
        if name == "lifeos.query":
            return self.query_service.query(str(arguments["question"]), limit=int(arguments.get("limit", 10)))
        if name == "lifeos.get_page":
            return self.query_service.get_page(str(arguments["path"]))
        if name == "lifeos.get_entity":
            return self.query_service.get_entity(str(arguments["entity"]))
        if name == "lifeos.context":
            return self.kernel.context(
                purpose=str(arguments["purpose"]),
                entities=[str(value) for value in arguments.get("entities") or []],
                previous_digest=str(arguments["previous_digest"]) if arguments.get("previous_digest") else None,
                max_tokens=int(arguments.get("max_tokens", 800)),
            ).to_dict()
        if name == "lifeos.list_proposals":
            return {"proposals": [proposal.to_dict() for proposal in self.proposals.list(arguments.get("status"))]}
        if name == "lifeos.get_proposal":
            return self.proposals.get(str(arguments["proposal_id"])).to_dict()
        if name == "lifeos.sources":
            packet = self.kernel.context(
                purpose=str(arguments["purpose"]),
                entities=[str(value) for value in arguments.get("entities") or []],
                max_tokens=200,
            )
            return {"coverage": packet.coverage, "evidence": packet.evidence, "digest": packet.digest}
        if name == "lifeos.connector_health":
            return self.connectors.health(str(arguments["connector"])).to_dict()
        if name == "lifeos.system_health":
            return self.system_health()
        raise KeyError(f"unknown MCP tool: {name}")

    def system_health(self) -> dict[str, Any]:
        connections = self.connectors.queue.list_connections()
        connector_health: dict[str, Any] = {}
        for connection in connections:
            key = str(connection["connector_key"])
            try:
                connector_health[key] = self.connectors.health(key).to_dict()
            except Exception as exc:
                connector_health[key] = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}
        return {
            "canon": {"state": "healthy", "path": str(self.brain)},
            "ingest": {
                "state": "healthy",
                "pending": self.connectors.queue.count("pending"),
                "processing": self.connectors.queue.count("processing"),
                "dead": self.connectors.queue.count("dead"),
                "total": self.connectors.queue.count(),
            },
            "staging": {
                "state": "healthy",
                "awaiting_review": len(self.proposals.list("awaiting_review")),
                "conflicts": len(self.proposals.list("conflict")),
            },
            "gbrain": {
                "state": "healthy" if self.query_service.gbrain.available() else "not_configured"
            },
            "connectors": connector_health,
        }


def handle_message(application: MCPApplication, message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "lifeos", "version": "0.2.0a1"},
                "instructions": "Markdown is canon. Staging is not canon. This MCP server is read-only.",
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOL_DEFINITIONS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = application.call(str(params.get("name") or ""), params.get("arguments") or {})
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}],
                    "structuredContent": result,
                    "isError": False,
                },
            }
        except Exception as exc:
            error = {"error": type(exc).__name__, "message": str(exc)}
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False)}],
                    "structuredContent": error,
                    "isError": True,
                },
            }
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(
    brain: Path | str | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> str | None:
    """Serve newline-delimited JSON-RPC over stdio.

    Calling without a brain preserves the v0.1 diagnostic summary behavior.
    """
    if brain is None:
        return "lifeos mcp tools: " + ", ".join(tool_names())
    application = MCPApplication(Path(brain))
    source = input_stream or sys.stdin
    destination = output_stream or sys.stdout
    for line in source:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = handle_message(application, message)
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"invalid request: {type(exc).__name__}"},
            }
        if response is not None:
            destination.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            destination.flush()
    return None
