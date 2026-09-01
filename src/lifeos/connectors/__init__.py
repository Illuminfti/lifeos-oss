"""LifeOS connector SDK.

Import connector classes from their modules, or discover installed plugins with
`ConnectorRegistry.discover()`.
"""

from lifeos.connectors.base import (
    BaseConnector,
    Connector,
    ConnectorContext,
    ConnectorRegistry,
)

__all__ = ["BaseConnector", "Connector", "ConnectorContext", "ConnectorRegistry"]
