"""Versioned public contracts for capture, proposals, and context packets."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping
from uuid import uuid5, NAMESPACE_URL

CONNECTOR_PROTOCOL = "lifeos.connector/v1"
CAPTURE_SCHEMA = "lifeos.capture-event/v1"
PROPOSAL_SCHEMA = "lifeos.proposal/v1"
CONTEXT_SCHEMA = "lifeos.turn-context/v1"

HEALTH_STATES = frozenset(
    {
        "healthy",
        "degraded",
        "auth_required",
        "rate_limited",
        "paused",
        "failed",
        "disconnected",
        "unsupported",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_iso8601(value: str) -> str:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_digest(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Actor:
    provider_ref: str
    display_name: str
    kind: str = "person"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_ref": self.provider_ref,
            "display_name": self.display_name,
            "kind": self.kind,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Attachment:
    blob_ref: str
    mime_type: str
    size: int = 0
    filename: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blob_ref": self.blob_ref,
            "mime_type": self.mime_type,
            "size": self.size,
            "filename": self.filename,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ConnectorManifest:
    id: str
    display_name: str
    source_classes: tuple[str, ...]
    capabilities: tuple[str, ...]
    auth_modes: tuple[str, ...]
    custody: str = "local"
    outbound_actions: bool = False
    implementation_status: str = "experimental"
    protocol: str = CONNECTOR_PROTOCOL
    version: str = "0.2.0"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.protocol != CONNECTOR_PROTOCOL:
            raise ValueError(f"unsupported connector protocol: {self.protocol}")
        if not self.id.startswith("org.lifeos."):
            raise ValueError("connector ids must begin with org.lifeos.")
        if self.outbound_actions:
            raise ValueError("capture connectors cannot expose outbound actions")
        if self.implementation_status not in {"working", "experimental", "scaffold"}:
            raise ValueError("invalid implementation_status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "id": self.id,
            "version": self.version,
            "display_name": self.display_name,
            "source_classes": list(self.source_classes),
            "capabilities": list(self.capabilities),
            "auth_modes": list(self.auth_modes),
            "custody": self.custody,
            "outbound_actions": self.outbound_actions,
            "implementation_status": self.implementation_status,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    state: str
    checked_at: str = field(default_factory=utc_now)
    last_success: str | None = None
    last_attempt: str | None = None
    error: str | None = None
    checkpoint: Mapping[str, Any] | None = None
    lag_seconds: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in HEALTH_STATES:
            raise ValueError(f"invalid health state: {self.state}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "checked_at": self.checked_at,
            "last_success": self.last_success,
            "last_attempt": self.last_attempt,
            "error": self.error,
            "checkpoint": dict(self.checkpoint) if self.checkpoint is not None else None,
            "lag_seconds": self.lag_seconds,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ConnectResult:
    connection_id: str
    settings: Mapping[str, Any]
    granted_scopes: tuple[str, ...] = ()
    secret_payload: Mapping[str, Any] | None = None
    custody: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "settings": dict(self.settings),
            "granted_scopes": list(self.granted_scopes),
            "custody": self.custody,
            "secret_stored": self.secret_payload is not None,
        }


@dataclass(frozen=True, slots=True)
class Connection:
    connection_id: str
    connector_id: str
    settings: Mapping[str, Any]
    granted_scopes: tuple[str, ...] = ()
    secret_ref: str | None = None
    status: str = "connected"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class CaptureEvent:
    event_id: str
    connector_id: str
    connection_id: str
    source_record_id: str
    source_revision: str
    kind: str
    occurred_at: str
    observed_at: str
    text: str = ""
    source_thread_id: str | None = None
    actors: tuple[Actor, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    deleted: bool = False
    visibility: str = "private"
    content_hash: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema: str = CAPTURE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != CAPTURE_SCHEMA:
            raise ValueError(f"unsupported capture schema: {self.schema}")
        if not self.connector_id.startswith("org.lifeos."):
            raise ValueError("invalid connector id")
        ensure_iso8601(self.occurred_at)
        ensure_iso8601(self.observed_at)
        if not self.source_record_id:
            raise ValueError("source_record_id is required")

    @classmethod
    def create(
        cls,
        *,
        connector_id: str,
        connection_id: str,
        source_record_id: str,
        kind: str,
        occurred_at: str,
        text: str = "",
        source_revision: str | None = None,
        source_thread_id: str | None = None,
        actors: Iterable[Actor] = (),
        attachments: Iterable[Attachment] = (),
        deleted: bool = False,
        visibility: str = "private",
        raw: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> "CaptureEvent":
        actor_tuple = tuple(actors)
        attachment_tuple = tuple(attachments)
        occurred = ensure_iso8601(occurred_at)
        observed = ensure_iso8601(observed_at or utc_now())
        payload = {
            "connector_id": connector_id,
            "connection_id": connection_id,
            "source_record_id": source_record_id,
            "kind": kind,
            "occurred_at": occurred,
            "text": text,
            "thread": source_thread_id,
            "actors": [a.to_dict() for a in actor_tuple],
            "attachments": [a.to_dict() for a in attachment_tuple],
            "deleted": deleted,
            "raw": dict(raw or {}),
            "metadata": dict(metadata or {}),
        }
        digest = content_digest(payload)
        revision = source_revision or digest
        identity = f"{connector_id}/{connection_id}/{source_record_id}/{revision}/{digest}"
        event_id = "evt_" + uuid5(NAMESPACE_URL, identity).hex
        return cls(
            event_id=event_id,
            connector_id=connector_id,
            connection_id=connection_id,
            source_record_id=source_record_id,
            source_revision=revision,
            source_thread_id=source_thread_id,
            kind=kind,
            occurred_at=occurred,
            observed_at=observed,
            actors=actor_tuple,
            text=text,
            attachments=attachment_tuple,
            deleted=deleted,
            visibility=visibility,
            content_hash=digest,
            raw=dict(raw or {}),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "connector_id": self.connector_id,
            "connection_id": self.connection_id,
            "source_record_id": self.source_record_id,
            "source_revision": self.source_revision,
            "source_thread_id": self.source_thread_id,
            "kind": self.kind,
            "occurred_at": self.occurred_at,
            "observed_at": self.observed_at,
            "actors": [actor.to_dict() for actor in self.actors],
            "text": self.text,
            "attachments": [attachment.to_dict() for attachment in self.attachments],
            "deleted": self.deleted,
            "visibility": self.visibility,
            "content_hash": self.content_hash,
            "raw": dict(self.raw),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CaptureEvent":
        return cls(
            schema=str(value.get("schema", CAPTURE_SCHEMA)),
            event_id=str(value["event_id"]),
            connector_id=str(value["connector_id"]),
            connection_id=str(value["connection_id"]),
            source_record_id=str(value["source_record_id"]),
            source_revision=str(value["source_revision"]),
            source_thread_id=value.get("source_thread_id"),
            kind=str(value["kind"]),
            occurred_at=str(value["occurred_at"]),
            observed_at=str(value["observed_at"]),
            actors=tuple(Actor(**actor) for actor in value.get("actors", [])),
            text=str(value.get("text", "")),
            attachments=tuple(Attachment(**item) for item in value.get("attachments", [])),
            deleted=bool(value.get("deleted", False)),
            visibility=str(value.get("visibility", "private")),
            content_hash=str(value.get("content_hash", "")),
            raw=dict(value.get("raw", {})),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class SyncBatch:
    events: tuple[CaptureEvent, ...]
    checkpoint: Mapping[str, Any]
    complete: bool = True
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    proposal_type: str
    status: str
    connector_id: str
    connection_id: str
    target_path: str
    target_revision: str
    title: str
    summary: str
    evidence_event_ids: tuple[str, ...]
    staging_path: str
    created_at: str = field(default_factory=utc_now)
    reviewed_at: str | None = None
    schema: str = PROPOSAL_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_event_ids"] = list(self.evidence_event_ids)
        return value


@dataclass(frozen=True, slots=True)
class PromotionReceipt:
    receipt_id: str
    proposal_id: str
    reviewer: str
    target_path: str
    before_revision: str
    after_revision: str
    promoted_at: str
    evidence_event_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_event_ids"] = list(self.evidence_event_ids)
        return value


@dataclass(frozen=True, slots=True)
class ContextPacket:
    purpose: str
    as_of: str
    current_facts: tuple[Mapping[str, Any], ...]
    recent_changes: tuple[Mapping[str, Any], ...]
    open_loops: tuple[Mapping[str, Any], ...]
    constraints: tuple[Mapping[str, Any], ...]
    evidence: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]
    digest: str
    not_modified: bool = False
    schema: str = CONTEXT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "purpose": self.purpose,
            "as_of": self.as_of,
            "current_facts": [dict(x) for x in self.current_facts],
            "recent_changes": [dict(x) for x in self.recent_changes],
            "open_loops": [dict(x) for x in self.open_loops],
            "constraints": [dict(x) for x in self.constraints],
            "evidence": [dict(x) for x in self.evidence],
            "coverage": dict(self.coverage),
            "digest": self.digest,
            "not_modified": self.not_modified,
        }
