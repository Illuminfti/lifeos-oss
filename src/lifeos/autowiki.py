"""Evidence-to-proposal pipeline. Models draft staging; owners promote canon."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from lifeos.contracts import CaptureEvent, utc_now
from lifeos.errors import PromotionConflict, ProposalNotFound
from lifeos.ingest import IngestQueue
from lifeos.wiki import atomic_write, init_brain, render_frontmatter, revision, safe_path, slugify


@dataclass(slots=True)
class Proposal:
    proposal_id: str
    proposal_type: str
    status: str
    title: str
    target_path: str
    target_id: str
    base_revision: str
    evidence: list[dict[str, Any]]
    proposed: dict[str, Any]
    created_at: str
    updated_at: str
    model: dict[str, Any] = field(default_factory=dict)
    confidence: str = "medium"
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    interaction_count: int = 1
    source_classes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Proposal":
        return cls(**value)


class ProposalStore:
    def __init__(self, brain: Path):
        self.brain = init_brain(Path(brain))
        self.state_dir = self.brain / ".lifeos" / "proposals"
        self.receipts_dir = self.brain / ".lifeos" / "receipts"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.receipts_dir.mkdir(parents=True, exist_ok=True)

    def _state(self, proposal_id: str) -> Path:
        return self.state_dir / f"{proposal_id}.json"

    def _stage(self, proposal_id: str, title: str) -> Path:
        return self.brain / "02-staging" / "entities" / f"{proposal_id}-{slugify(title)}.md"

    def list(self, status: str | None = None) -> list[Proposal]:
        found = []
        for path in sorted(self.state_dir.glob("*.json")):
            proposal = Proposal.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if status is None or proposal.status == status:
                found.append(proposal)
        return found

    def get(self, proposal_id: str) -> Proposal:
        path = self._state(proposal_id)
        if not path.exists():
            raise ProposalNotFound(proposal_id)
        return Proposal.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, proposal: Proposal) -> Proposal:
        proposal.updated_at = utc_now()
        atomic_write(
            self._state(proposal.proposal_id),
            json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        atomic_write(self._stage(proposal.proposal_id, proposal.title), self.render(proposal))
        return proposal

    def render(self, proposal: Proposal) -> str:
        frontmatter = render_frontmatter(
            {
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type,
                "status": proposal.status,
                "target_id": proposal.target_id,
                "target_path": proposal.target_path,
                "base_revision": proposal.base_revision,
                "confidence": proposal.confidence,
                "interaction_count": proposal.interaction_count,
                "source_classes": proposal.source_classes,
                "created_at": proposal.created_at,
                "updated_at": proposal.updated_at,
            }
        )
        evidence = "\n".join(
            f"- `{item.get('event_id')}` from `{item.get('connector_id')}` at {item.get('occurred_at')}"
            for item in proposal.evidence
        )
        aliases = ", ".join(proposal.proposed.get("aliases") or []) or "None"
        conflicts = "\n".join(
            f"- {json.dumps(item, ensure_ascii=False, sort_keys=True)}" for item in proposal.conflicts
        ) or "None"
        return (
            f"{frontmatter}\n\n# Proposal: {proposal.title}\n\n"
            "**This is staging. It is not canon.**\n\n"
            f"## Proposed compiled truth\n\n{proposal.proposed.get('summary', '')}\n\n"
            f"## Aliases\n\n{aliases}\n\n"
            f"## Evidence\n\n{evidence or 'None'}\n\n"
            f"## Conflicts\n\n{conflicts}\n"
        )

    def reject(self, proposal_id: str, owner: str, reason: str = "") -> Proposal:
        proposal = self.get(proposal_id)
        proposal.status = "rejected"
        proposal.proposed["rejected_by"] = owner
        proposal.proposed["rejection_reason"] = reason
        return self.save(proposal)

    def promote(
        self,
        proposal_id: str,
        *,
        owner: str,
        confirm: bool = False,
        edited_summary: str | None = None,
        aliases: list[str] | None = None,
        on_promoted: Callable[[Path], None] | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            raise PermissionError("owner promotion requires confirm=True")
        proposal = self.get(proposal_id)
        if proposal.status != "awaiting_review":
            raise PromotionConflict(f"proposal is {proposal.status}, not awaiting_review")
        target = safe_path(self.brain, proposal.target_path)
        current = revision(target)
        if current != proposal.base_revision:
            proposal.status = "conflict"
            proposal.conflicts.append({"kind": "revision_changed", "expected": proposal.base_revision, "actual": current})
            self.save(proposal)
            raise PromotionConflict("canonical target changed after proposal creation")
        before = target.read_text(encoding="utf-8") if target.exists() else None
        summary = edited_summary if edited_summary is not None else str(proposal.proposed.get("summary") or "")
        final_aliases = list(aliases if aliases is not None else proposal.proposed.get("aliases") or [])
        source_ids = sorted({str(item["event_id"]) for item in proposal.evidence})
        now = utc_now()
        frontmatter = render_frontmatter(
            {
                "id": proposal.target_id,
                "title": proposal.title,
                "type": "person",
                "status": "canonical",
                "aliases": final_aliases,
                "sources": source_ids,
                "confidence": proposal.confidence,
                "reviewed": now[:10],
                "next_review": (datetime.now(timezone.utc) + timedelta(days=90)).date().isoformat(),
                "sensitivity": "private",
            }
        )
        timeline = "\n".join(
            f"- {item.get('occurred_at')}: interaction via `{item.get('connector_id')}` (`{item.get('event_id')}`)"
            for item in sorted(proposal.evidence, key=lambda value: str(value.get("occurred_at")))
        )
        content = f"{frontmatter}\n\n# {proposal.title}\n\n## Current\n\n{summary}\n\n## Timeline\n\n{timeline}\n"
        atomic_write(target, content)
        after = revision(target)
        receipt = {
            "receipt_id": "prm_" + uuid4().hex,
            "proposal_id": proposal_id,
            "owner": owner,
            "promoted_at": now,
            "target_path": proposal.target_path,
            "before_revision": current,
            "after_revision": after,
            "before_content": before,
            "after_content": content,
            "evidence_hashes": [item.get("content_hash") for item in proposal.evidence],
        }
        receipt_path = self.receipts_dir / f"{now.replace(':', '').replace('-', '')}-{proposal_id}.json"
        atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        proposal.status = "promoted"
        proposal.proposed["promotion_receipt"] = str(receipt_path.relative_to(self.brain))
        self.save(proposal)
        if on_promoted:
            on_promoted(target)
        return {"proposal": proposal.to_dict(), "target": str(target), "revision": after, "receipt": str(receipt_path)}

    def reverse(self, receipt_path: str, *, owner: str, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise PermissionError("reversal requires confirm=True")
        path = safe_path(self.brain, receipt_path)
        receipt = json.loads(path.read_text(encoding="utf-8"))
        target = safe_path(self.brain, receipt["target_path"])
        if revision(target) != receipt["after_revision"]:
            raise PromotionConflict("canonical target changed after promotion")
        before = receipt.get("before_content")
        if before is None:
            if target.exists():
                target.unlink()
        else:
            atomic_write(target, before)
        reverse = {
            "receipt_id": "rev_" + uuid4().hex,
            "reverses": receipt["receipt_id"],
            "owner": owner,
            "reversed_at": utc_now(),
            "target_path": receipt["target_path"],
            "result_revision": revision(target),
        }
        output = self.receipts_dir / f"{utc_now().replace(':', '').replace('-', '')}-{reverse['receipt_id']}.json"
        atomic_write(output, json.dumps(reverse, indent=2, sort_keys=True) + "\n")
        return reverse


class AutoWiki:
    def __init__(self, brain: Path, model: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        self.brain = init_brain(Path(brain))
        self.store = ProposalStore(self.brain)
        self.model = model

    def store_raw(self, event: CaptureEvent) -> Path:
        day = event.occurred_at[:10].replace("-", "/") if len(event.occurred_at) >= 10 else "unknown"
        path = self.brain / "07-raw" / event.connector_id.removeprefix("org.lifeos.") / day / f"{event.event_id}.json"
        atomic_write(path, json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return path

    def process(self, event: CaptureEvent) -> list[Proposal]:
        raw = self.store_raw(event)
        created = []
        if event.deleted:
            return created
        for actor in event.actors:
            if actor.display_name.strip():
                created.append(self._entity_proposal(event, actor, raw))
        if not created and event.text.strip():
            capture = self.brain / "01-inbox" / "captures" / f"{event.event_id}.md"
            atomic_write(
                capture,
                f"---\nevent_id: {event.event_id}\nconnector: {event.connector_id}\nstatus: captured\n---\n\n# Capture\n\n{event.text}\n",
            )
        return created

    def _entity_proposal(self, event, actor, raw):
        identity = actor.provider_ref or actor.display_name.casefold()
        target_id = "ent_" + sha256(identity.encode()).hexdigest()[:16]
        target = f"03-entities/people/{slugify(actor.display_name)}-{target_id[-6:]}.md"
        existing = next(
            (proposal for proposal in self.store.list("awaiting_review") if proposal.target_id == target_id),
            None,
        )
        evidence = {
            "event_id": event.event_id,
            "connector_id": event.connector_id,
            "connection_id": event.connection_id,
            "occurred_at": event.occurred_at,
            "content_hash": event.content_hash,
            "raw_path": str(raw.relative_to(self.brain)),
            "excerpt": event.text[:500],
        }
        if existing:
            if event.event_id not in {item["event_id"] for item in existing.evidence}:
                existing.evidence.append(evidence)
                existing.interaction_count += 1
                existing.source_classes = sorted(set(existing.source_classes) | {event.connector_id})
            return self.store.save(existing)
        proposed = {
            "summary": f"{actor.display_name} appears in captured activity. Review the evidence before treating identity or relationship claims as fact.",
            "aliases": [actor.display_name],
            "provider_refs": [actor.provider_ref] if actor.provider_ref else [],
        }
        model_metadata = {}
        if self.model:
            result = self.model(
                {
                    "task": "draft_entity_proposal",
                    "actor": actor.to_dict(),
                    "event": {"event_id": event.event_id, "text": event.text[:4000], "occurred_at": event.occurred_at},
                    "allowed_output": ["summary", "aliases"],
                }
            )
            if not isinstance(result, dict) or set(result) - {"summary", "aliases", "model"}:
                raise ValueError("model may only write proposal summary and aliases")
            if result.get("summary"):
                proposed["summary"] = str(result["summary"])
            if isinstance(result.get("aliases"), list):
                proposed["aliases"] = [str(value) for value in result["aliases"]]
            model_metadata = dict(result.get("model") or {})
        now = utc_now()
        return self.store.save(
            Proposal(
                proposal_id="prop_" + uuid4().hex[:16],
                proposal_type="entity_update",
                status="awaiting_review",
                title=actor.display_name,
                target_path=target,
                target_id=target_id,
                base_revision=revision(self.brain / target),
                evidence=[evidence],
                proposed=proposed,
                created_at=now,
                updated_at=now,
                model=model_metadata,
                interaction_count=1,
                source_classes=[event.connector_id],
            )
        )

    @staticmethod
    def enrichment_due(proposal: Proposal, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        sources = len(set(proposal.source_classes))
        dates = []
        for evidence in proposal.evidence:
            try:
                dates.append(datetime.fromisoformat(str(evidence.get("occurred_at")).replace("Z", "+00:00")))
            except ValueError:
                pass
        recent = any(now - date <= timedelta(days=30) for date in dates)
        return proposal.interaction_count >= 5 or (proposal.interaction_count >= 3 and sources >= 2 and recent)


class AutoWikiWorker:
    def __init__(self, queue: IngestQueue, autowiki: AutoWiki):
        self.queue = queue
        self.autowiki = autowiki

    def work(self, limit: int = 20) -> dict[str, int]:
        processed = failed = proposals = 0
        for event in self.queue.claim(limit=limit):
            try:
                proposals += len(self.autowiki.process(event))
                self.queue.ack(event.event_id)
                processed += 1
            except Exception as exc:
                self.queue.fail(event.event_id, str(exc))
                failed += 1
        return {"processed": processed, "failed": failed, "proposals": proposals, "remaining": self.queue.count("pending")}
