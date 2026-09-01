"""lifeos.connector/v1 contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONNECTOR_PROTOCOL = "lifeos.connector/v1"
CAPTURE_SCHEMA = "lifeos.capture-event/v1"

HEALTH_STATES = (
    "healthy",
    "degraded",
    "auth_required",
    "rate_limited",
    "paused",
    "failed",
    "disconnected",
)


@dataclass
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


@dataclass
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


@dataclass
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CAPTURE_SCHEMA,
            "event_id": self.event_id,
            "connector_id": self.connector_id,
            "source_record_id": self.source_record_id,
            "kind": self.kind,
            "occurred_at": self.occurred_at,
            "observed_at": self.observed_at,
            "text": self.text,
            "deleted": self.deleted,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }
