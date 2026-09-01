"""Subject-delta packetization and owner-attention backpressure.

Operations are grouped by semantic dependency, not merely by their first
subject ID. A relation that depends on two new subjects therefore travels with
both spawn operations in one owner-reviewable transaction.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from lifeos.evidence import EvidenceStore
from lifeos.ids import new_id
from lifeos.semantic import Operation, ReviewPacket, utc_now

_SUBJECT_PREFIXES = (
    "cand_",
    "per_",
    "org_",
    "col_",
    "con_",
    "prj_",
    "fun_",
    "ast_",
    "plc_",
    "evt_",
    "dec_",
    "lop_",
)
_KIND_PRECEDENCE = {
    "routine_delta": 0,
    "identity_spawn": 1,
    "urgent_commitment": 2,
    "conflict": 3,
}


def _semantic_references(value: Any) -> set[str]:
    """Return candidate/canonical subject references from an operation payload.

    Evidence, claim, packet, and operation IDs have different prefixes and do
    not accidentally join otherwise independent changes.
    """
    found: set[str] = set()
    if isinstance(value, str):
        if value.startswith(_SUBJECT_PREFIXES):
            found.add(value)
    elif isinstance(value, dict):
        for nested in value.values():
            found.update(_semantic_references(nested))
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            found.update(_semantic_references(nested))
    return found


class Packetizer:
    VERSION = "review-packets/v2.1"

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

    @staticmethod
    def operation_references(operation: Operation) -> set[str]:
        references = _semantic_references(operation.payload)
        if operation.subject_id and operation.subject_id.startswith(_SUBJECT_PREFIXES):
            references.add(operation.subject_id)
        return references

    def _routine_threshold(self) -> float:
        backlog = len(self.store.list_review_packets(limit=1000, packet_kind="routine_delta"))
        if backlog <= self.routine_backlog_soft_limit:
            return 0.0
        overflow = backlog - self.routine_backlog_soft_limit
        return min(0.9, 0.45 + overflow / max(100, self.routine_backlog_soft_limit * 4))

    def _components(self, operations: list[Operation]) -> list[list[Operation]]:
        """Union operations that share any candidate or canonical subject."""
        if not operations:
            return []
        parent = list(range(len(operations)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        first_for_reference: dict[str, int] = {}
        for index, operation in enumerate(operations):
            for reference in self.operation_references(operation):
                prior = first_for_reference.setdefault(reference, index)
                union(index, prior)

        grouped: dict[int, list[Operation]] = defaultdict(list)
        for index, operation in enumerate(operations):
            grouped[find(index)].append(operation)
        return list(grouped.values())

    def _component_kind(self, operations: list[Operation]) -> str:
        return max(
            (self.packet_kind(operation) for operation in operations),
            key=lambda kind: _KIND_PRECEDENCE[kind],
        )

    def _component_subject(self, operations: list[Operation]) -> str | None:
        spawn_candidates = sorted(
            {
                str(operation.payload["candidate_id"])
                for operation in operations
                if operation.kind == "spawn_subject" and operation.payload.get("candidate_id")
            }
        )
        if spawn_candidates:
            return spawn_candidates[0]
        direct = sorted({operation.subject_id for operation in operations if operation.subject_id})
        if direct:
            return direct[0]
        references = sorted(
            reference
            for operation in operations
            for reference in self.operation_references(operation)
        )
        return references[0] if references else None

    def packetize(
        self,
        operations: list[Operation],
        *,
        source_event_count: int = 1,
        ignored_event_count: int = 0,
    ) -> list[ReviewPacket]:
        packets: list[ReviewPacket] = []
        routine_threshold = self._routine_threshold()
        for items in self._components(operations):
            kind = self._component_kind(items)
            priority = max(item.priority for item in items)
            if kind == "routine_delta" and priority < routine_threshold:
                # Proposals remain in operational state and may accumulate
                # stronger evidence without becoming another owner inbox item.
                continue
            subject_id = self._component_subject(items)
            now = utc_now()
            safe_count = sum(1 for item in items if item.safe)
            reference_count = len(
                {
                    reference
                    for item in items
                    for reference in self.operation_references(item)
                }
            )
            summary = (
                f"{len(items)} connected state change(s) across {reference_count} subject(s); "
                f"{safe_count} mechanically safe. Reduced from "
                f"{source_event_count} source event(s)."
            )
            packet = ReviewPacket(
                packet_id=new_id("pkt"),
                packet_kind=kind,
                subject_id=subject_id,
                priority=priority,
                state="open",
                operations=items,
                created_at=now,
                updated_at=now,
                expected_review_seconds=min(120, 10 + len(items) * 8),
                summary=summary,
                source_event_count=source_event_count,
                ignored_event_count=ignored_event_count,
            )
            packets.append(self.store.upsert_review_packet(packet))
        return packets
