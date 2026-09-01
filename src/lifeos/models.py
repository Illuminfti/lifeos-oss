"""Provider-neutral structured extraction ports."""
from __future__ import annotations

from dataclasses import dataclass
import json
import shlex
import subprocess
from typing import Any, Mapping, Protocol

from lifeos.contracts import CaptureEvent
from lifeos.errors import ConnectorError


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    provider_ref: str
    display_name: str
    entity_type: str = "person"
    confidence: str = "medium"
    summary: str = ""


class Extractor(Protocol):
    def entities(
        self, event: CaptureEvent, *, existing: Mapping[str, Any] | None = None
    ) -> tuple[EntityCandidate, ...]: ...


class DeterministicExtractor:
    """Extracts only explicit actors already supplied by a connector."""

    def entities(
        self, event: CaptureEvent, *, existing: Mapping[str, Any] | None = None
    ) -> tuple[EntityCandidate, ...]:
        return tuple(
            EntityCandidate(
                provider_ref=actor.provider_ref,
                display_name=actor.display_name,
                entity_type="person" if actor.kind == "person" else "organization",
                confidence="high",
                summary=f"Explicit actor in {event.kind} evidence.",
            )
            for actor in event.actors
            if actor.provider_ref and actor.display_name
        )


class CommandExtractor:
    """Runs a user-configured local structured extractor command.

    Event JSON is written to stdin. The command must return a JSON object with an
    `entities` array. LifeOS validates the shape and still writes only staging.
    """

    def __init__(self, command: str | list[str], *, timeout_seconds: int = 60):
        self.command = shlex.split(command) if isinstance(command, str) else list(command)
        self.timeout_seconds = timeout_seconds
        if not self.command:
            raise ValueError("extractor command is empty")

    def entities(
        self, event: CaptureEvent, *, existing: Mapping[str, Any] | None = None
    ) -> tuple[EntityCandidate, ...]:
        request = {"schema": "lifeos.extract/v1", "event": event.to_dict(), "existing": dict(existing or {})}
        try:
            completed = subprocess.run(
                self.command,
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConnectorError(f"extractor command failed: {exc}") from exc
        if completed.returncode != 0:
            raise ConnectorError(
                f"extractor exited {completed.returncode}: {completed.stderr[:1000]}"
            )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ConnectorError("extractor returned invalid JSON") from exc
        if not isinstance(response, Mapping) or not isinstance(response.get("entities", []), list):
            raise ConnectorError("extractor response must contain an entities array")
        candidates: list[EntityCandidate] = []
        for item in response.get("entities", []):
            if not isinstance(item, Mapping) or not item.get("provider_ref") or not item.get("display_name"):
                continue
            entity_type = str(item.get("entity_type", "person"))
            if entity_type not in {"person", "organization", "place", "tool", "asset", "media"}:
                continue
            candidates.append(
                EntityCandidate(
                    provider_ref=str(item["provider_ref"]),
                    display_name=str(item["display_name"]),
                    entity_type=entity_type,
                    confidence=str(item.get("confidence", "medium")),
                    summary=str(item.get("summary", "")),
                )
            )
        return tuple(candidates)
