"""Connector plugin protocol and dynamic registry."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from lifeos.config import BrainConfig
from lifeos.contracts import (
    ConnectResult,
    Connection,
    ConnectorManifest,
    HealthReport,
    SyncBatch,
)
from lifeos.errors import ConnectorError
from lifeos.secrets import SecretStore
from lifeos.storage import StateStore

ENTRYPOINT_GROUP = "lifeos.connectors"


@dataclass(slots=True)
class ConnectorContext:
    config: BrainConfig
    store: StateStore
    secrets: SecretStore

    def secret_for(self, connection: Connection) -> dict[str, Any]:
        if not connection.secret_ref:
            return {}
        return self.secrets.get(connection.secret_ref)


@runtime_checkable
class Connector(Protocol):
    @property
    def manifest(self) -> ConnectorManifest: ...

    def connect(
        self, request: Mapping[str, Any], context: ConnectorContext
    ) -> ConnectResult: ...

    def backfill(
        self,
        connection: Connection,
        checkpoint: Mapping[str, Any],
        context: ConnectorContext,
    ) -> SyncBatch: ...

    def sync(
        self,
        connection: Connection,
        checkpoint: Mapping[str, Any],
        context: ConnectorContext,
    ) -> SyncBatch: ...

    def health(
        self, connection: Connection | None, context: ConnectorContext
    ) -> HealthReport: ...

    def revoke(self, connection: Connection, context: ConnectorContext) -> Mapping[str, Any]: ...

    def purge(self, connection: Connection, context: ConnectorContext) -> Mapping[str, Any]: ...

    def test_fixture(self, context: ConnectorContext) -> Mapping[str, Any]: ...

    def verify_webhook(
        self,
        connection: Connection,
        headers: Mapping[str, str],
        body: bytes,
        context: ConnectorContext,
    ) -> bool: ...

    def webhook_challenge(
        self,
        connection: Connection,
        query: Mapping[str, str],
        context: ConnectorContext,
    ) -> str | None: ...


class BaseConnector:
    manifest: ConnectorManifest

    def sync(
        self,
        connection: Connection,
        checkpoint: Mapping[str, Any],
        context: ConnectorContext,
    ) -> SyncBatch:
        return self.backfill(connection, checkpoint, context)

    def health(
        self, connection: Connection | None, context: ConnectorContext
    ) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        if connection.status == "paused":
            return HealthReport(state="paused")
        if connection.status == "revoked":
            return HealthReport(state="disconnected")
        return HealthReport(state="healthy")

    def revoke(self, connection: Connection, context: ConnectorContext) -> Mapping[str, Any]:
        return {
            "ok": True,
            "remote_revocation": "not_supported",
            "credentials_deleted_by_core": True,
        }

    def purge(self, connection: Connection, context: ConnectorContext) -> Mapping[str, Any]:
        return {"ok": True, "remote_data_deleted": False, "local_purge_owned_by_core": True}

    def verify_webhook(
        self,
        connection: Connection,
        headers: Mapping[str, str],
        body: bytes,
        context: ConnectorContext,
    ) -> bool:
        return False

    def webhook_challenge(
        self,
        connection: Connection,
        query: Mapping[str, str],
        context: ConnectorContext,
    ) -> str | None:
        return None

    def test_fixture(self, context: ConnectorContext) -> Mapping[str, Any]:
        return {
            "ok": True,
            "connector_id": self.manifest.id,
            "protocol": self.manifest.protocol,
            "implementation_status": self.manifest.implementation_status,
        }


@dataclass(frozen=True, slots=True)
class ConnectorRegistration:
    name: str
    connector: Connector
    distribution: str


class ConnectorRegistry:
    """Discovers bundled and third-party plugins through Python entry points.

    Provider names live in package metadata, not in the core registry. A third
    party can ship a separate wheel with a `lifeos.connectors` entry point.
    """

    def __init__(self, registrations: Mapping[str, ConnectorRegistration]):
        self._items = dict(registrations)

    @classmethod
    def discover(cls) -> "ConnectorRegistry":
        registrations: dict[str, ConnectorRegistration] = {}
        entries = metadata.entry_points()
        selected = entries.select(group=ENTRYPOINT_GROUP) if hasattr(entries, "select") else entries.get(ENTRYPOINT_GROUP, [])
        for entry in selected:
            loaded = entry.load()
            connector = loaded() if isinstance(loaded, type) else loaded
            if not isinstance(connector, Connector):
                raise ConnectorError(f"entry point {entry.name!r} does not implement Connector")
            if entry.name in registrations:
                raise ConnectorError(f"duplicate connector entry point: {entry.name}")
            if any(
                existing.connector.manifest.id == connector.manifest.id
                for existing in registrations.values()
            ):
                raise ConnectorError(f"duplicate connector id: {connector.manifest.id}")
            distribution = getattr(getattr(entry, "dist", None), "name", None) or "unknown"
            registrations[entry.name] = ConnectorRegistration(
                name=entry.name,
                connector=connector,
                distribution=str(distribution),
            )
        return cls(registrations)

    @classmethod
    def from_connectors(cls, connectors: Mapping[str, Connector]) -> "ConnectorRegistry":
        return cls(
            {
                name: ConnectorRegistration(name, connector, "test")
                for name, connector in connectors.items()
            }
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def registrations(self) -> tuple[ConnectorRegistration, ...]:
        return tuple(self._items[name] for name in self.names())

    def get(self, name: str) -> Connector:
        try:
            return self._items[name].connector
        except KeyError as exc:
            installed = ", ".join(self.names()) or "none"
            raise ConnectorError(f"connector {name!r} is not installed; installed: {installed}") from exc

    def find_by_id(self, connector_id: str) -> tuple[str, Connector]:
        for name, registration in self._items.items():
            if registration.connector.manifest.id == connector_id:
                return name, registration.connector
        raise ConnectorError(f"connector id {connector_id!r} is not installed")
