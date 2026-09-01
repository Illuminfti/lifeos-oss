"""Curated MCP-shaped tool list. No canonical put_page."""

TOOLS = [
    "lifeos.search",
    "lifeos.query",
    "lifeos.get_page",
    "lifeos.get_entity",
    "lifeos.context",
    "lifeos.list_proposals",
    "lifeos.get_proposal",
    "lifeos.sources",
    "lifeos.connector_health",
    "lifeos.system_health",
]


def tool_names() -> list[str]:
    return list(TOOLS)


def serve() -> str:
    return "lifeos mcp tools: " + ", ".join(TOOLS)
