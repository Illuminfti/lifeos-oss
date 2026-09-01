from __future__ import annotations

import asyncio

import pytest

from lifeos.connectors.base import ConnectorRegistry
from lifeos.connectors.example import ExampleConnector
from lifeos.mcp_server import READ_TOOLS, STAGING_TOOLS, create_server
from lifeos.runtime import LifeOSRuntime


def test_default_mcp_surface_has_no_promotion_or_outbound_tool():
    names = set(READ_TOOLS)
    assert "lifeos.context" in names
    assert not any("promote" in name for name in names)
    assert not any(word in name for name in names for word in ("send", "post", "purchase"))
    assert "lifeos.capture_note" not in names
    assert STAGING_TOOLS == ("lifeos.capture_note",)


def test_real_mcp_v2_server_lists_only_the_curated_read_surface(brain):
    pytest.importorskip("mcp")
    from mcp import Client

    registry = ConnectorRegistry.from_connectors({"example": ExampleConnector()})
    with LifeOSRuntime(brain, registry=registry) as runtime:
        server = create_server(runtime, profile="read")

        async def listed_names() -> set[str]:
            async with Client(server) as client:
                response = await client.list_tools()
                return {tool.name for tool in response.tools}

        assert asyncio.run(listed_names()) == set(READ_TOOLS)
