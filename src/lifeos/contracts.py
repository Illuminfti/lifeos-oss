"""Versioned contracts shared by core, plugins, CLI, and MCP."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Iterator, Mapping
from uuid import uuid4

CONNECTOR_PROTOCOL = "lifeos.connector/v1"
CAPTURE_SCHEMA = "lifeos.capture-event/v1"
CONTEXT_SCHEMA = "lifeos.turn-context/v1"
PLUGIN_ENTRYPOINT_GROUP = "lifeos.connectors"
HEALTH_STATES = {"healthy", "degraded", "auth_required", "rate_limited", "paused", "failed", "disconnected"}


class ContractError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(raw.encode("utf-8")).hexdigest()


class DictLike:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())


@dataclass(slots=True)
class ConnectorManifest(DictLike):
    id: str
    display_name: str
    source_classes: list[str]
    capabilities: list[str]
    auth_modes: list[str]
    custody: str = "local"
    outbound_actions: bool = False
    protocol: str = CONNECTOR_PROTOCOL
    version: str = "0.2.0"
    notes: str = ""
    config_schema: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.protocol != CONNECTOR_PROTOCOL:
            raise ContractError(f"unsupported protocol: {self.protocol}")
        if not self.id.startswith("org.lifeos."):
            raise ContractError("connector id must start org.lifeos.")
        if self.outbound_actions:
            raise ContractError("capture plugins cannot expose outbound actions")
        if not self.source_classes:
            raise ContractError("source_classes may not be empty")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(slots=True)
class HealthReport(DictLike):
    state: str
    last_success: str | None = None
    last_attempt: str | None = None
    error: str | None = None
    checkpoint: dict[str, Any] | None = None
    lag_seconds: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        if self.state not in HEALTH_STATES:
            raise ContractError(f"invalid health state: {self.state}")
        return asdict(self)


@dataclass(slots=True)
class ConnectionReceipt(DictLike):
    ok: bool
    connection_id: str | None = None
    state: str = "disconnected"
    custody: str = "local"
    scopes: list[str] = field(default_factory=list)
    public_config: dict[str, Any] = field(default_factory=dict)
    provider_identity: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    message: str | None = None


@dataclass(slots=True)
class CaptureActor(DictLike):
    display_name: str
    provider_ref: str | None = None
    role: str | None = None
    canonical_hint: str | None = None


@dataclass(slots=True)
class AttachmentRef(DictLike):
    blob_ref: str
    mime_type: str | None = None
    size: int | None = None
    name: str | None = None
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CaptureEvent(DictLike):
    event_id: str
    connector_id: str
    source_record_id: str
    kind: str
    occurred_at: str
    observed_at: str
    text: str = ""
    connection_id: str = "default"
    source_revision: str = ""
    source_thread_id: str | None = None
    actors: list[CaptureActor] = field(default_factory=list)
    attachments: list[AttachmentRef] = field(default_factory=list)
    deleted: bool = False
    visibility: str = "private"
    content_hash: str = ""
    raw_blob_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        required = [self.event_id, self.connector_id, self.source_record_id, self.kind, self.occurred_at, self.observed_at, self.content_hash]
        if not all(required):
            raise ContractError("capture event missing required field")
        if self.visibility not in {"private", "restricted", "public"}:
            raise ContractError("invalid visibility")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["schema"] = CAPTURE_SCHEMA
        return payload

    @classmethod
    def build(
        cls,
        *,
        connector_id: str,
        source_record_id: str,
        kind: str,
        occurred_at: str,
        text: str = "",
        connection_id: str = "default",
        source_revision: str = "",
        source_thread_id: str | None = None,
        actors: list[CaptureActor] | None = None,
        attachments: list[AttachmentRef] | None = None,
        deleted: bool = False,
        visibility: str = "private",
        raw_blob_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> "CaptureEvent":
        normalized = {
            "connector_id": connector_id,
            "connection_id": connection_id,
            "source_record_id": source_record_id,
            "source_revision": source_revision,
            "kind": kind,
            "occurred_at": occurred_at,
            "text": text,
            "deleted": deleted,
            "metadata": dict(metadata or {}),
        }
        return cls(
            event_id="evt_" + uuid4().hex,
            connector_id=connector_id,
            connection_id=connection_id,
            source_record_id=source_record_id,
            source_revision=source_revision,
            source_thread_id=source_thread_id,
            kind=kind,
            occurred_at=occurred_at,
            observed_at=observed_at or utc_now(),
            text=text,
            actors=list(actors or []),
            attachments=list(attachments or []),
            deleted=deleted,
            visibility=visibility,
            content_hash=stable_hash(normalized),
            raw_blob_ref=raw_blob_ref,
            metadata=dict(metadata or {}),
        )


@dataclass(slots=True)
class SyncBatch(DictLike):
    events: list[CaptureEvent] = field(default_factory=list)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    complete: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "checkpoint": dict(self.checkpoint),
            "complete": self.complete,
            "warnings": list(self.warnings),
        }

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self.events[key]
        return self.to_dict()[key]


@dataclass(slots=True)
class ContextPacket(DictLike):
    packet_id: str
    purpose: str
    as_of: str
    current_facts: list[dict[str, Any]]
    recent_changes: list[dict[str, Any]]
    open_loops: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    coverage: dict[str, Any]
    digest: str
    schema: str = CONTEXT_SCHEMA
    status: str = "ok"

    @classmethod
    def unchanged(cls, purpose: str, digest: str) -> "ContextPacket":
        return cls(
            packet_id="pkt_" + uuid4().hex,
            purpose=purpose,
            as_of=utc_now(),
            current_facts=[],
            recent_changes=[],
            open_loops=[],
            constraints=[],
            evidence=[],
            coverage={"current_sources": [], "stale_sources": [], "denied_sources": [], "failed_sources": []},
            digest=digest,
            status="not_modified",
        )
