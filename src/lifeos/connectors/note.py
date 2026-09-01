"""Manual note capture connector used by CLI and staging MCP profile."""
from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch


class NoteConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.note",
        display_name="Manual notes",
        source_classes=("note",),
        capabilities=("incremental_capture", "revoke", "purge"),
        auth_modes=("local",),
        custody="local",
        implementation_status="working",
        notes="Local notes enter the same ingest path and remain non-canonical until review.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={},
            granted_scopes=("note:capture",),
        )

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return SyncBatch(events=(), checkpoint=dict(checkpoint))

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return SyncBatch(events=(), checkpoint=dict(checkpoint))

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        return HealthReport(state="healthy" if connection else "disconnected")
