"""Single durable ingest path and worker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from lifeos.autowiki import AutoWiki
from lifeos.config import BrainConfig
from lifeos.contracts import CaptureEvent, SyncBatch
from lifeos.raw_store import RawStore
from lifeos.storage import StateStore


@dataclass(frozen=True, slots=True)
class IngestReceipt:
    accepted: int
    duplicates: int
    checkpoint_committed: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "checkpoint_committed": self.checkpoint_committed,
            "warnings": list(self.warnings),
        }


class IngestService:
    def __init__(self, config: BrainConfig, store: StateStore):
        self.config = config
        self.store = store
        self.raw = RawStore(config)

    def accept_batch(self, connection_id: str, stream: str, batch: SyncBatch) -> IngestReceipt:
        accepted = 0
        duplicates = 0
        for event in batch.events:
            if event.connection_id != connection_id:
                raise ValueError("connector emitted an event for a different connection")
            raw_path = self.raw.put(event)
            if self.store.accept_event(event, raw_path=raw_path):
                accepted += 1
            else:
                duplicates += 1
        # The checkpoint moves only after every event is durably present.
        self.store.put_checkpoint(connection_id, stream, batch.checkpoint)
        return IngestReceipt(
            accepted=accepted,
            duplicates=duplicates,
            checkpoint_committed=True,
            warnings=batch.warnings,
        )

    def process(self, autowiki: AutoWiki, *, limit: int = 100, owner: str | None = None) -> dict[str, int]:
        worker = owner or "worker_" + uuid4().hex
        leased = self.store.lease_events(
            owner=worker,
            limit=limit,
            lease_seconds=int(self.config.get("ingest", "lease_seconds", default=60)),
        )
        processed = 0
        failed = 0
        dead = 0
        for event in leased:
            try:
                autowiki.process_event(event)
            except Exception as exc:
                state = self.store.retry_event(
                    event.event_id,
                    owner=worker,
                    error=f"{type(exc).__name__}: {exc}",
                    max_attempts=int(self.config.get("ingest", "max_attempts", default=5)),
                )
                failed += 1
                dead += int(state == "dead")
            else:
                if self.store.ack_event(event.event_id, owner=worker):
                    processed += 1
        return {"leased": len(leased), "processed": processed, "failed": failed, "dead": dead}
