"""Derived, provenance-carrying views. Insights never become canon implicitly."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from statistics import fmean

from lifeos.evidence import EvidenceStore
from lifeos.projection import ProjectionReader
from lifeos.semantic import InsightRecord


class InsightEngine:
    VERSION = "insights/v2"

    def __init__(self, index_path: Path, evidence_store: EvidenceStore | None = None):
        self.reader = ProjectionReader(index_path)
        self.index_path = Path(index_path)
        self.evidence_store = evidence_store

    def _revision_hash(self) -> str:
        return self.reader.metadata().get("canon_revision_hash", "unknown")

    def relationship_radar(self, person_id: str) -> InsightRecord:
        with self.reader._connect() as conn:
            relation_rows = conn.execute(
                """SELECT * FROM canon_relation
                   WHERE status IN ('active', 'disputed')
                     AND (subject_id = ? OR object_ref = ?)
                   ORDER BY COALESCE(valid_from, ''), claim_id""",
                (person_id, person_id),
            ).fetchall()
            loop_rows = conn.execute(
                """SELECT DISTINCT l.subject_id AS loop_id, c.claim_id, c.predicate,
                                  l.title, c.recorded_at
                   FROM canon_subject l
                   JOIN canon_claim c ON c.subject_id = l.subject_id
                   WHERE l.type = 'open_loop' AND l.status = 'active'
                     AND c.object_ref = ?
                     AND c.predicate IN ('owed_by', 'owed_to', 'waiting_on', 'about')
                     AND c.status IN ('active', 'disputed')""",
                (person_id,),
            ).fetchall()
        relation_claims = [dict(row) for row in relation_rows]
        loops = [dict(row) for row in loop_rows]
        disputed = sum(1 for row in relation_claims if row["status"] == "disputed")
        body = (
            f"This relationship view has {len(relation_claims)} active or disputed canonical "
            f"relation claim(s) and {len(loops)} unresolved linked loop(s)."
        )
        if disputed:
            body += f" {disputed} relation claim(s) are disputed and need human interpretation."
        return InsightRecord.create(
            algorithm="relationship-radar/v2",
            canon_revision_hash=self._revision_hash(),
            title=f"Relationship radar: {person_id}",
            body=body,
            confidence=0.82 if relation_claims else 0.45,
            input_claim_ids=[row["claim_id"] for row in relation_claims]
            + [row["claim_id"] for row in loops],
            limitations=[
                "Facets describe recorded evidence, not the quality or moral value of a relationship.",
                "Quiet but important interactions may be absent from connected sources.",
            ],
            dimensions={
                "relation_claims": len(relation_claims),
                "disputed_relations": disputed,
                "linked_open_loops": len(loops),
                "open_loop_ids": [row["loop_id"] for row in loops],
            },
        )

    def self_pattern(self, metric_a: str, metric_b: str) -> InsightRecord:
        if self.evidence_store is None:
            raise RuntimeError("self-pattern analysis requires the evidence store")
        a = self._daily_metric(metric_a)
        b = self._daily_metric(metric_b)
        dates = sorted(set(a) & set(b))
        pairs = [(a[date], b[date]) for date in dates]
        if len(pairs) < 5:
            return InsightRecord.create(
                algorithm="self-pattern-correlation/v2",
                canon_revision_hash=self._revision_hash(),
                title=f"Self pattern: {metric_a} and {metric_b}",
                body=f"Only {len(pairs)} aligned day(s) are available, which is too little for a useful association.",
                confidence=0.1,
                input_observation_ids=self._observation_ids(metric_a) + self._observation_ids(metric_b),
                limitations=["Minimum sample size is five aligned days.", "No causal conclusion is made."],
                dimensions={"sample_size": len(pairs), "association": None},
            )
        xs = [pair[0] for pair in pairs]
        ys = [pair[1] for pair in pairs]
        correlation = self._pearson(xs, ys)
        direction = "increase together" if correlation > 0 else "move in opposite directions"
        body = (
            f"Across {len(pairs)} aligned day(s), {metric_a} and {metric_b} have tended to "
            f"{direction} (Pearson r={correlation:.2f}). This is an association, not a causal claim."
        )
        return InsightRecord.create(
            algorithm="self-pattern-correlation/v2",
            canon_revision_hash=self._revision_hash(),
            title=f"Self pattern: {metric_a} and {metric_b}",
            body=body,
            confidence=min(0.9, 0.35 + len(pairs) / 50),
            input_observation_ids=self._observation_ids(metric_a) + self._observation_ids(metric_b),
            limitations=[
                "Correlation does not establish causation.",
                "Daily averaging can hide timing and lag effects.",
                "Connected-source coverage may be incomplete.",
            ],
            dimensions={
                "sample_size": len(pairs),
                "pearson_r": correlation,
                "metric_a": metric_a,
                "metric_b": metric_b,
            },
        )

    def life_function_coverage(self) -> list[InsightRecord]:
        with self.reader._connect() as conn:
            functions = conn.execute(
                "SELECT * FROM canon_subject WHERE type = 'life_function' AND status = 'active' ORDER BY importance DESC"
            ).fetchall()
            results: list[InsightRecord] = []
            for function in functions:
                linked = conn.execute(
                    """SELECT c.claim_id, c.subject_id, s.type, s.status, c.recorded_at
                       FROM canon_claim c
                       JOIN canon_subject s ON s.subject_id = c.subject_id
                       WHERE c.predicate = 'belongs_to_function'
                         AND c.object_ref = ?
                         AND c.status = 'active'""",
                    (function["subject_id"],),
                ).fetchall()
                open_loops = [row for row in linked if row["type"] == "open_loop" and row["status"] == "active"]
                last_change = max((row["recorded_at"] for row in linked), default=None)
                body = (
                    f"{function['title']} has {len(linked)} active linked item(s), including "
                    f"{len(open_loops)} open loop(s)."
                )
                results.append(
                    InsightRecord.create(
                        algorithm="life-function-coverage/v2",
                        canon_revision_hash=self._revision_hash(),
                        title=f"Life function: {function['title']}",
                        body=body,
                        confidence=0.78 if linked else 0.45,
                        input_claim_ids=[row["claim_id"] for row in linked],
                        limitations=[
                            "Coverage reflects canonical links, not a universal ideal balance.",
                            "Owner-defined cadence claims should govern neglect thresholds.",
                        ],
                        dimensions={
                            "function_id": function["subject_id"],
                            "importance": function["importance"],
                            "linked_items": len(linked),
                            "open_loops": len(open_loops),
                            "last_recorded_change": last_change,
                        },
                    )
                )
        return results

    def circumstance_changes(self, *, days: int = 30) -> InsightRecord:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        with self.reader._connect() as conn:
            rows = conn.execute(
                """SELECT claim_id, subject_id, predicate, status, recorded_at
                   FROM canon_claim WHERE recorded_at >= ?
                   ORDER BY recorded_at DESC, claim_id""",
                (cutoff,),
            ).fetchall()
        changes = [dict(row) for row in rows]
        predicates: dict[str, int] = defaultdict(int)
        for row in changes:
            predicates[row["predicate"]] += 1
        top = sorted(predicates.items(), key=lambda item: (-item[1], item[0]))[:5]
        body = f"{len(changes)} canonical claim change(s) were recorded in the last {days} day(s)."
        if top:
            body += " Most common change classes: " + ", ".join(f"{name} ({count})" for name, count in top) + "."
        return InsightRecord.create(
            algorithm="circumstance-change-feed/v2",
            canon_revision_hash=self._revision_hash(),
            title="Material circumstance changes",
            body=body,
            confidence=0.85,
            input_claim_ids=[row["claim_id"] for row in changes],
            limitations=[
                "A recent recording date is not always the same as the real-world change date.",
                "Materiality ranking depends on owner importance and predicate policy.",
            ],
            dimensions={"window_days": days, "change_count": len(changes), "top_predicates": top},
        )

    def decision_outcomes(self) -> list[InsightRecord]:
        with self.reader._connect() as conn:
            decisions = conn.execute(
                "SELECT * FROM canon_subject WHERE type = 'decision' ORDER BY revision DESC"
            ).fetchall()
            output: list[InsightRecord] = []
            for decision in decisions:
                claims = conn.execute(
                    """SELECT claim_id, predicate, object_json, status
                       FROM canon_claim WHERE subject_id = ?
                         AND predicate IN ('intended_outcome', 'observed_outcome', 'supersedes')""",
                    (decision["subject_id"],),
                ).fetchall()
                intended = sum(1 for row in claims if row["predicate"] == "intended_outcome")
                observed = sum(1 for row in claims if row["predicate"] == "observed_outcome")
                body = (
                    f"{decision['title']} records {intended} intended outcome(s) and "
                    f"{observed} observed outcome(s)."
                )
                if intended and not observed:
                    body += " The intended outcome has not yet been measured in canon."
                output.append(
                    InsightRecord.create(
                        algorithm="decision-outcome-ledger/v2",
                        canon_revision_hash=self._revision_hash(),
                        title=f"Decision outcome: {decision['title']}",
                        body=body,
                        confidence=0.8,
                        input_claim_ids=[row["claim_id"] for row in claims],
                        limitations=["Missing outcomes may reflect missing evidence rather than failure."],
                        dimensions={
                            "decision_id": decision["subject_id"],
                            "intended_outcomes": intended,
                            "observed_outcomes": observed,
                        },
                    )
                )
        return output

    def leverage_map(self, *, limit: int = 10) -> InsightRecord:
        with self.reader._connect() as conn:
            rows = conn.execute(
                """SELECT subject_id, object_ref, claim_id
                   FROM canon_relation
                   WHERE status = 'active'
                     AND predicate IN (
                       'participates_in', 'responsible_for', 'depends_on',
                       'contributes_to', 'belongs_to_function', 'uses_asset',
                       'waiting_on', 'blocks'
                     )"""
            ).fetchall()
            titles = {
                row["subject_id"]: row["title"]
                for row in conn.execute("SELECT subject_id, title FROM canon_subject")
            }
        degree: dict[str, int] = defaultdict(int)
        claim_ids: list[str] = []
        for row in rows:
            degree[row["subject_id"]] += 1
            if row["object_ref"]:
                degree[row["object_ref"]] += 1
            claim_ids.append(row["claim_id"])
        ranked = sorted(degree.items(), key=lambda item: (-item[1], item[0]))[:limit]
        body = "Highest-connectivity subjects: " + (
            ", ".join(f"{titles.get(subject_id, subject_id)} ({score})" for subject_id, score in ranked)
            if ranked
            else "none yet"
        ) + "."
        return InsightRecord.create(
            algorithm="leverage-map/v2",
            canon_revision_hash=self._revision_hash(),
            title="Leverage map",
            body=body,
            confidence=0.7 if ranked else 0.25,
            input_claim_ids=claim_ids,
            limitations=[
                "Connectivity is a prompt for attention, not proof of importance or causality.",
                "Derived graph degree never changes canonical truth.",
            ],
            dimensions={
                "ranked_subjects": [
                    {"subject_id": subject_id, "title": titles.get(subject_id), "degree": score}
                    for subject_id, score in ranked
                ]
            },
        )

    def _daily_metric(self, metric: str) -> dict[str, float]:
        if self.evidence_store is None:
            return {}
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in self.evidence_store.list_observations(metric):
            if row.get("value") is not None:
                grouped[str(row["observed_at"])[:10]].append(float(row["value"]))
        return {day: fmean(values) for day, values in grouped.items()}

    def _observation_ids(self, metric: str) -> list[str]:
        if self.evidence_store is None:
            return []
        return [row["observation_id"] for row in self.evidence_store.list_observations(metric)]

    @staticmethod
    def _pearson(xs: list[float], ys: list[float]) -> float:
        mean_x = fmean(xs)
        mean_y = fmean(ys)
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
        denominator = math.sqrt(
            sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
        )
        return 0.0 if denominator == 0 else numerator / denominator
