"""Open-source read-only LifeOS Intelligence Kernel surface."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from lifeos.config import BrainConfig
from lifeos.contracts import ContextPacket, canonical_json, content_digest, utc_now
from lifeos.errors import GBrainUnavailable
from lifeos.retrieval import GBrainAdapter, PgGraphAdapter


def _flatten_results(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item if isinstance(item, Mapping) else {"text": str(item)} for item in value]
    if isinstance(value, Mapping):
        for key in ("results", "data", "items", "matches"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return [item if isinstance(item, Mapping) else {"text": str(item)} for item in candidate]
        return [value]
    if value:
        return [{"text": str(value)}]
    return []


def _bounded(items: Iterable[Mapping[str, Any]], max_chars: int, max_results: int) -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    used = 0
    for item in items:
        if len(output) >= max_results:
            break
        normalized = dict(item)
        encoded = canonical_json(normalized)
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(encoded) > remaining:
            text = str(normalized.get("text") or normalized.get("content") or normalized.get("snippet") or encoded)
            normalized = {"text": text[: max(0, remaining - 100)], "truncated": True}
            encoded = canonical_json(normalized)
        output.append(normalized)
        used += len(encoded)
    return tuple(output)


class LifeOSIntelligenceKernel:
    """Purpose-scoped, compact, provenance-preserving, and read-only.

    This public implementation deliberately keeps the authority line small. It
    asks GBrain for evidence, never writes canon, never executes an action, and
    reports missing retrieval rather than fabricating context.
    """

    def __init__(
        self,
        config: BrainConfig,
        *,
        gbrain: GBrainAdapter | None = None,
        pggraph: PgGraphAdapter | None = None,
    ):
        self.config = config
        self.gbrain = gbrain or GBrainAdapter(config)
        self.pggraph = pggraph or PgGraphAdapter(config)

    def turn_context(
        self,
        *,
        purpose: str,
        subjects: tuple[str, ...] = (),
        known_digest: str | None = None,
    ) -> ContextPacket:
        purpose = purpose.strip()
        if not purpose:
            raise ValueError("purpose is required")
        query = purpose
        if subjects:
            query += "\nNamed subjects: " + ", ".join(subjects)
        max_chars = min(8000, max(512, int(self.config.get("kernel", "max_chars", default=4000))))
        max_results = int(self.config.get("kernel", "max_results", default=8))
        coverage: dict[str, Any] = {
            "current_sources": [],
            "stale_sources": [],
            "denied_sources": [],
            "failed_sources": [],
            "pggraph": self.pggraph.health(),
        }
        try:
            raw = self.gbrain.query(query, limit=max_results)
            evidence = _bounded(_flatten_results(raw), max_chars, max_results)
            coverage["current_sources"].append("gbrain")
        except GBrainUnavailable as exc:
            evidence = ()
            coverage["failed_sources"].append({"source": "gbrain", "error": str(exc)})
        facts: list[Mapping[str, Any]] = []
        for index, item in enumerate(evidence):
            claim = item.get("answer") or item.get("text") or item.get("content") or item.get("snippet")
            if claim:
                facts.append(
                    {
                        "claim": str(claim),
                        "confidence": item.get("confidence", "unknown"),
                        "evidence_refs": [item.get("path") or item.get("slug") or f"gbrain:{index}"],
                    }
                )
        digest_payload = {
            "purpose": purpose,
            "subjects": list(subjects),
            "facts": facts,
            "evidence": evidence,
            "coverage": coverage,
        }
        digest = content_digest(digest_payload)
        if known_digest and known_digest == digest:
            return ContextPacket(
                purpose=purpose,
                as_of=utc_now(),
                current_facts=(),
                recent_changes=(),
                open_loops=(),
                constraints=(),
                evidence=(),
                coverage=coverage,
                digest=digest,
                not_modified=True,
            )
        return ContextPacket(
            purpose=purpose,
            as_of=utc_now(),
            current_facts=tuple(facts),
            recent_changes=(),
            open_loops=(),
            constraints=(
                {"rule": "canonical Markdown wins over GBrain and pgGraph"},
                {"rule": "packet is read-only and cannot authorize external action"},
            ),
            evidence=evidence,
            coverage=coverage,
            digest=digest,
        )
