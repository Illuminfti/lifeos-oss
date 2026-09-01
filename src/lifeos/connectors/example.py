"""Synthetic reference connector for authors and conformance tests."""
from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import (
    Actor,
    CaptureEvent,
    ConnectResult,
    Connection,
    ConnectorManifest,
    HealthReport,
    SyncBatch,
)


class ExampleConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.example",
        display_name="Example synthetic connector",
        source_classes=("message", "person"),
        capabilities=("backfill", "incremental_sync", "revoke", "purge", "fixtures"),
        auth_modes=("none",),
        custody="local",
        implementation_status="working",
        notes="Synthetic only. Contains no personal data.",
    )

    def connect(
        self, request: Mapping[str, Any], context: ConnectorContext
    ) -> ConnectResult:
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={"fixture_count": int(request.get("fixture_count", 1))},
            granted_scopes=("synthetic:read",),
        )

    def _events(self, connection: Connection, count: int) -> tuple[CaptureEvent, ...]:
        return tuple(
            CaptureEvent.create(
                connector_id=self.manifest.id,
                connection_id=connection.connection_id,
                source_record_id=f"fixture-{index}",
                source_revision="1",
                source_thread_id="fixture-thread",
                kind="message.created",
                occurred_at=f"2026-01-{index + 1:02d}T12:00:00Z",
                actors=(Actor(provider_ref="fixture:ada", display_name="Ada Example"),),
                text=f"Synthetic fixture message {index + 1}. No personal data.",
                metadata={"fixture": True},
            )
            for index in range(count)
        )

    def backfill(
        self,
        connection: Connection,
        checkpoint: Mapping[str, Any],
        context: ConnectorContext,
    ) -> SyncBatch:
        count = int(connection.settings.get("fixture_count", 1))
        if checkpoint.get("complete"):
            return SyncBatch(events=(), checkpoint=dict(checkpoint), complete=True)
        return SyncBatch(
            events=self._events(connection, count),
            checkpoint={"complete": True, "count": count},
        )

    def sync(
        self,
        connection: Connection,
        checkpoint: Mapping[str, Any],
        context: ConnectorContext,
    ) -> SyncBatch:
        return SyncBatch(events=(), checkpoint=dict(checkpoint), complete=True)

    def health(
        self, connection: Connection | None, context: ConnectorContext
    ) -> HealthReport:
        return HealthReport(state="healthy" if connection else "disconnected")
