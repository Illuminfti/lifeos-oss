"""Provider-neutral connector and evidence contracts.

Connectors terminate at CaptureEvent. They never choose canonical pages, entity
IDs, or ontology types. The v2 envelope preserves provider context while
remaining backward-compatible with v1 connector fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any

CONNECTOR_PROTOCOL = "lifeos.connector/v1"
CAPTURE_SCHEMA = "lifeos.capture-event/v2"

HEALTH_STATES = (
    "healthy",
    "degraded",
    "auth_required",
    "rate_limited",
    "paused",
    "failed",
    "disconnected",
)


@dataclass(slots=True)
class ConnectorManifest:
    id: str
    display_name: str
    source_classes: list[str]
    capabilities: list[str]
    auth_modes: list[str]
    custody: str = "local"
    outbound_actions: bool = False
    protocol: str = CONNECTOR_PROTOCOL
    version: str = "0.1.0"
    notes: str = ""

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
            "notes": self.notes,
        }


@dataclass(slots=True)
class HealthReport:
    state: str
    last_success: str | None = None
    last_attempt: str | None = None
    error: str | None = None
    checkpoint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.state not in HEALTH_STATES:
            raise ValueError(f"invalid health state: {self.state}")
        return {
            "state": self.state,
            "last_success": self.last_success,
            "last_attempt": self.last_attempt,
            "error": self.error,
            "checkpoint": self.checkpoint,
        }


@dataclass(slots=True)
class CaptureEvent:
    event_id: str
    connector_id: str
    source_record_id: str
    kind: str
    occurred_at: str
    observed_at: str
    text: str = ""
    deleted: bool = False
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # v2 scope and lineage. Defaults keep every v1 connector fixture valid.
    brain_id: str = "brain_default"
    connection_id: str = "con_default"
    source_revision: str = "1"
    supersedes_event_id: str | None = None
    correlation_id: str | None = None
    raw_ref: str | None = None
    visibility: str = "private"
    sensitivity: str = "personal"
    actors: list[dict[str, Any]] = field(default_factory=list)
    conversation: dict[str, Any] | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)

    def canonical_content(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "occurred_at": self.occurred_at,
            "text": self.text,
            "deleted": bool(self.deleted),
            "metadata": self.metadata,
            "actors": self.actors,
            "conversation": self.conversation,
            "attachments": self.attachments,
            "links": self.links,
            "raw_ref": self.raw_ref,
        }

    def computed_content_hash(self) -> str:
        payload = json.dumps(
            self.canonical_content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return sha256(payload).hexdigest()

    def normalized_content_hash(self) -> str:
        return self.content_hash or self.computed_content_hash()

    def validate(self) -> None:
        required = {
            "event_id": self.event_id,
            "connector_id": self.connector_id,
            "source_record_id": self.source_record_id,
            "kind": self.kind,
            "observed_at": self.observed_at,
            "brain_id": self.brain_id,
            "connection_id": self.connection_id,
            "source_revision": self.source_revision,
        }
        missing = [key for key, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"capture event missing required fields: {', '.join(missing)}")
        if self.visibility not in {"private", "shared", "public"}:
            raise ValueError(f"invalid visibility: {self.visibility}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": CAPTURE_SCHEMA,
            "specversion": "1.0",
            "id": self.event_id,
            "source": f"lifeos://connector/{self.connector_id}/{self.connection_id}",
            "type": self.kind,
            "subject": self.source_record_id,
            "time": self.occurred_at,
            "datacontenttype": "application/json",
            "event_id": self.event_id,
            "brain_id": self.brain_id,
            "connector_id": self.connector_id,
            "connection_id": self.connection_id,
            "source_record_id": self.source_record_id,
            "source_revision": self.source_revision,
            "kind": self.kind,
            "occurred_at": self.occurred_at,
            "observed_at": self.observed_at,
            "supersedes_event_id": self.supersedes_event_id,
            "correlation_id": self.correlation_id,
            "text": self.text,
            "deleted": bool(self.deleted),
            "content_hash": self.normalized_content_hash(),
            "raw_ref": self.raw_ref,
            "visibility": self.visibility,
            "sensitivity": self.sensitivity,
            "actors": list(self.actors),
            "conversation": self.conversation,
            "attachments": list(self.attachments),
            "links": list(self.links),
            "metadata": dict(self.metadata),
        }
