from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from lifeos.contracts import CaptureEvent, ConnectorManifest, HealthReport


class Plugin:
    """WhatsApp Business capture plugin. Outbound send is out of scope."""

    def __init__(self) -> None:
        self.manifest = ConnectorManifest(
            id="org.lifeos.whatsapp-business",
            display_name="WhatsApp Business",
            source_classes=['message', 'thread'],
            capabilities=['incremental_sync', 'revoke', 'purge'],
            auth_modes=['oauth'],
            custody="third_party",
            outbound_actions=False,
            notes="WABA webhooks for new business events. Not personal-account history.",
        )
        self._connected = False
        self._secret_ref: str | None = None

    def describe(self) -> ConnectorManifest:
        return self.manifest

    def health(self) -> HealthReport:
        if not self._connected:
            return HealthReport(state="disconnected")
        if self.manifest.id.endswith(".example"):
            return HealthReport(state="healthy")
        return HealthReport(
            state="auth_required",
            error="credentials not configured; live sync is not faked",
        )

    def connect(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.manifest.id.endswith(".example"):
            self._connected = True
            return {"ok": True, "connection_id": "con_example", "custody": "local"}
        secret = (
            request.get("secret_ref")
            or request.get("path")
            or request.get("export_path")
            or request.get("socket")
        )
        if not secret:
            return {
                "ok": False,
                "error": "auth_required",
                "message": "No credentials supplied. Capture plugins do not invent sessions.",
            }
        self._connected = True
        self._secret_ref = str(secret)
        return {
            "ok": True,
            "connection_id": "con_pending",
            "custody": self.manifest.custody,
            "live_sync": False,
            "note": "Credential handle accepted. Live provider client is not claimed until implemented.",
        }

    def backfill(self, request: dict[str, Any]) -> list[CaptureEvent]:
        if self.manifest.id.endswith(".example"):
            return [self._fixture_event()]
        return []

    def sync(self, request: dict[str, Any]) -> list[CaptureEvent]:
        return []

    def revoke(self) -> dict[str, Any]:
        self._connected = False
        self._secret_ref = None
        return {"ok": True, "credentials_deleted": True, "evidence_untouched": True}

    def purge(self) -> dict[str, Any]:
        return {"ok": True, "raw_deleted": True, "canon_untouched": True}

    def test_fixture(self) -> dict[str, Any]:
        ev = self._fixture_event()
        return {"ok": True, "events": 1, "event_id": ev.event_id, "kind": ev.kind}

    def _fixture_event(self) -> CaptureEvent:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = "synthetic fixture; not personal data (whatsapp-business)"
        return CaptureEvent(
            event_id="evt_" + uuid4().hex[:12],
            connector_id=self.manifest.id,
            source_record_id="fix_1",
            kind="fixture.created",
            occurred_at=now,
            observed_at=now,
            text=text,
            content_hash=sha256(text.encode()).hexdigest(),
            metadata={"connector": "whatsapp-business", "synthetic": True},
        )
