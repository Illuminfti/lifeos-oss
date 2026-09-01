"""Subject-delta packetization and owner-attention backpressure."""
from __future__ import annotations

from collections import defaultdict

from lifeos.evidence import EvidenceStore
from lifeos.ids import new_id
from lifeos.semantic import Operation, ReviewPacket, utc_now


class Packetizer:
    VERSION = "review-packets/v2"

    def __init__(self, store: EvidenceStore, *, routine_backlog_soft_limit: int = 50):
        self.store = store
        self.routine_backlog_soft_limit = routine_backlog_soft_limit

    @staticmethod
    def packet_kind(operation: Operation) -> str:
        if operation.kind in {"raise_conflict", "retract_claim", "source_revoked"}:
            return "conflict"
        if operation.kind in {"resolve_identity", "spawn_subject", "merge_subjects"}:
            return "identity_spawn"
        if operation.kind in {"create_open_loop", "update_open_loop"} and operation.priority >= 0.8:
            return "urgent_commitment"
        return "routine_delta"

    def _routine_threshold(self) -> float:
        backlog = len(self.store.list_review_packets(limit=1000, packet_kind="routine_delta"))
        if backlog <= self.routine_backlog_soft_limit:
            return 0.0
        overflow = backlog - self.routine_backlog_soft_limit
        return min(0.9, 0.45 + overflow / max(100, self.routine_backlog_soft_limit * 4))

    def packetize(
        self,
        operations: list[Operation],
        *,
        source_event_count: int = 1,
        ignored_event_count: int = 0,
    ) -> list[ReviewPacket]:
        grouped: dict[tuple[str, str | None], list[Operation]] = defaultdict(list)
        routine_threshold = self._routine_threshold()
        for operation in operations:
            kind = self.packet_kind(operation)
            if kind == "routine_delta" and operation.priority < routine_threshold:
                # The proposal remains in operational state and can accumulate
                # more evidence. It does not become a second inbox item.
                continue
            grouped[(kind, operation.subject_id)].append(operation)

        packets: list[ReviewPacket] = []
        for (kind, subject_id), items in grouped.items():
            now = utc_now()
            safe_count = sum(1 for item in items if item.safe)
            summary = (
                f"{len(items)} proposed state change(s); {safe_count} mechanically safe. "
                f"Reduced from {source_event_count} source event(s)."
            )
            packet = ReviewPacket(
                packet_id=new_id("pkt"),
                packet_kind=kind,
                subject_id=subject_id,
                priority=max(item.priority for item in items),
                state="open",
                operations=items,
                created_at=now,
                updated_at=now,
                expected_review_seconds=min(90, 10 + len(items) * 8),
                summary=summary,
                source_event_count=source_event_count,
                ignored_event_count=ignored_event_count,
            )
            packets.append(self.store.upsert_review_packet(packet))
        return packets
