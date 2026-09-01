"""Composition root for one LifeOS brain."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from lifeos.autowiki import AutoWiki
from lifeos.config import BrainConfig, atomic_write_text, load_config
from lifeos.connectors.base import ConnectorContext, ConnectorRegistry
from lifeos.contracts import Connection, HealthReport, utc_now
from lifeos.errors import ConfigurationError, ConnectorError
from lifeos.ingest import IngestReceipt, IngestService
from lifeos.kernel import LifeOSIntelligenceKernel
from lifeos.raw_store import RawStore
from lifeos.retrieval import GBrainAdapter, PgGraphAdapter
from lifeos.secrets import FileSecretStore
from lifeos.storage import StateStore
from lifeos.wiki import render_frontmatter


@dataclass(slots=True)
class SyncResult:
    connection_id: str
    stream: str
    events_emitted: int
    complete: bool
    ingest: IngestReceipt
    processed: Mapping[str, int] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "stream": self.stream,
            "events_emitted": self.events_emitted,
            "complete": self.complete,
            "ingest": self.ingest.to_dict(),
            "processed": dict(self.processed) if self.processed else None,
        }


class LifeOSRuntime:
    def __init__(
        self,
        config: BrainConfig,
        *,
        registry: ConnectorRegistry | None = None,
    ):
        self.config = config
        self.store = StateStore(config.db_path)
        self.secrets = FileSecretStore(config.secrets_path)
        self.registry = registry or ConnectorRegistry.discover()
        self.context = ConnectorContext(config, self.store, self.secrets)
        self.autowiki = AutoWiki(config, self.store)
        self.ingest = IngestService(config, self.store)
        self.gbrain = GBrainAdapter(config)
        self.pggraph = PgGraphAdapter(config)
        self.kernel = LifeOSIntelligenceKernel(config, gbrain=self.gbrain, pggraph=self.pggraph)

    @classmethod
    def open(
        cls,
        brain: str | Path,
        *,
        registry: ConnectorRegistry | None = None,
    ) -> "LifeOSRuntime":
        return cls(load_config(brain), registry=registry)

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "LifeOSRuntime":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def connect(self, name: str, request: Mapping[str, Any]) -> Connection:
        connector = self.registry.get(name)
        result = connector.connect(dict(request), self.context)
        secret_ref: str | None = None
        try:
            if result.secret_payload:
                secret_ref = self.secrets.put(
                    result.secret_payload,
                    label=f"{connector.manifest.id}:{result.connection_id}",
                )
            connection = Connection(
                connection_id=result.connection_id,
                connector_id=connector.manifest.id,
                settings=dict(result.settings),
                granted_scopes=result.granted_scopes,
                secret_ref=secret_ref,
                status="connected",
            )
            self.store.put_connection(connection, connector_name=name)
            return connection
        except Exception:
            if secret_ref:
                self.secrets.delete(secret_ref)
            raise

    def _connection(self, connection_id: str):
        found = self.store.get_connection(connection_id)
        if not found:
            raise ConnectorError(f"unknown connection: {connection_id}")
        name, connection = found
        connector = self.registry.get(name)
        if connector.manifest.id != connection.connector_id:
            raise ConnectorError("connection manifest no longer matches installed connector")
        return name, connector, connection

    def run_connector(
        self,
        connection_id: str,
        *,
        stream: str,
        process: bool = True,
        process_limit: int = 1000,
    ) -> SyncResult:
        if stream not in {"backfill", "sync"}:
            raise ValueError("stream must be backfill or sync")
        _, connector, connection = self._connection(connection_id)
        if connection.status in {"paused", "revoked", "purged"}:
            raise ConnectorError(f"connection is {connection.status}")
        checkpoint = self.store.get_checkpoint(connection_id, stream)
        if stream == "sync" and not checkpoint:
            checkpoint = self.store.get_checkpoint(connection_id, "backfill")
        batch = (
            connector.backfill(connection, checkpoint, self.context)
            if stream == "backfill"
            else connector.sync(connection, checkpoint, self.context)
        )
        for event in batch.events:
            if event.connector_id != connector.manifest.id:
                raise ConnectorError("connector emitted an event with the wrong connector id")
        receipt = self.ingest.accept_batch(connection_id, stream, batch)
        processed = self.ingest.process(self.autowiki, limit=process_limit) if process else None
        return SyncResult(
            connection_id=connection_id,
            stream=stream,
            events_emitted=len(batch.events),
            complete=batch.complete,
            ingest=receipt,
            processed=processed,
        )

    def process(self, *, limit: int = 100) -> Mapping[str, int]:
        return self.ingest.process(self.autowiki, limit=limit)

    def health(self, connection_id: str) -> HealthReport:
        _, connector, connection = self._connection(connection_id)
        return connector.health(connection, self.context)

    def pause(self, connection_id: str) -> None:
        self._connection(connection_id)
        self.store.set_connection_status(connection_id, "paused")

    def resume(self, connection_id: str) -> None:
        self._connection(connection_id)
        self.store.set_connection_status(connection_id, "connected")

    def revoke(self, connection_id: str) -> Mapping[str, Any]:
        _, connector, connection = self._connection(connection_id)
        result = dict(connector.revoke(connection, self.context))
        credentials_deleted = bool(connection.secret_ref and self.secrets.delete(connection.secret_ref))
        scrubbed = Connection(
            connection_id=connection.connection_id,
            connector_id=connection.connector_id,
            settings=connection.settings,
            granted_scopes=connection.granted_scopes,
            secret_ref=None,
            status="revoked",
            created_at=connection.created_at,
            updated_at=utc_now(),
        )
        name, _ = self.registry.find_by_id(connection.connector_id)
        self.store.put_connection(scrubbed, connector_name=name)
        return {**result, "credentials_deleted": credentials_deleted, "evidence_untouched": True}

    def purge(self, connection_id: str) -> Mapping[str, Any]:
        name, connector, connection = self._connection(connection_id)
        provider = dict(connector.purge(connection, self.context))
        raw_deleted = RawStore(self.config).delete_for_connection(connection_id)
        credentials_deleted = bool(connection.secret_ref and self.secrets.delete(connection.secret_ref))
        counts = self.store.purge_connection_data(connection_id)
        review_path = self.config.resolve_inside(f"02-staging/deletions/purge-{connection_id}.md")
        review = render_frontmatter(
            {
                "status": "awaiting_review",
                "type": "source_purge",
                "connection_id": connection_id,
                "connector": connection.connector_id,
                "created_at": utc_now(),
                "sensitivity": "private",
            }
        )
        atomic_write_text(
            review_path,
            f"{review}\n\n# Canon review after source purge\n\nSource evidence for `{connection_id}` was purged. Canon was deliberately not deleted. Review canonical claims whose only evidence came from this source.\n",
            mode=0o600,
        )
        purged = Connection(
            connection_id=connection.connection_id,
            connector_id=connection.connector_id,
            settings={},
            granted_scopes=(),
            secret_ref=None,
            status="purged",
            created_at=connection.created_at,
            updated_at=utc_now(),
        )
        self.store.put_connection(purged, connector_name=name)
        return {
            "provider": provider,
            "raw_deleted": raw_deleted,
            "credentials_deleted": credentials_deleted,
            "operational_deleted": counts,
            "canon_deleted": False,
            "canon_review": review_path.relative_to(self.config.root).as_posix(),
        }

    def doctor(self) -> Mapping[str, Any]:
        required = [
            "CANON.md",
            "02-staging",
            "03-entities",
            "07-raw",
            ".lifeos/config.json",
        ]
        tree = {relative: (self.config.root / relative).exists() for relative in required}
        connections: list[dict[str, Any]] = []
        for name, connection in self.store.list_connections(include_revoked=True):
            try:
                connector = self.registry.get(name)
                health = connector.health(connection, self.context).to_dict()
            except Exception as exc:
                health = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}
            connections.append(
                {
                    "connection_id": connection.connection_id,
                    "connector": name,
                    "status": connection.status,
                    "health": health,
                }
            )
        return {
            "ok": all(tree.values()) and self.config.db_path.exists(),
            "brain": str(self.config.root),
            "tree": tree,
            "state": self.store.stats(),
            "plugins": [
                {
                    "name": registration.name,
                    "id": registration.connector.manifest.id,
                    "status": registration.connector.manifest.implementation_status,
                    "distribution": registration.distribution,
                }
                for registration in self.registry.registrations()
            ],
            "connections": connections,
            "gbrain": self.gbrain.doctor(),
            "pggraph": self.pggraph.health(),
            "kernel": {"read_only": True, "action_plane": "disconnected"},
        }
