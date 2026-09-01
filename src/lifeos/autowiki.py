"""Review-gated auto-wiki pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5

from lifeos.config import BrainConfig, atomic_write_text
from lifeos.contracts import CaptureEvent, Proposal, PromotionReceipt, content_digest, utc_now
from lifeos.errors import ConfigurationError, ProposalNotFound, StaleProposal
from lifeos.models import DeterministicExtractor, EntityCandidate, Extractor
from lifeos.storage import StateStore
from lifeos.wiki import file_revision, render_entity_page, render_frontmatter, slugify, write_canonical

CANONICAL_START = "<!-- LIFEOS:CANONICAL_START -->"
CANONICAL_END = "<!-- LIFEOS:CANONICAL_END -->"

ENTITY_DIRS = {
    "person": "people",
    "organization": "organizations",
    "place": "places",
    "tool": "tools",
    "asset": "assets",
    "media": "media",
}


def _excerpt(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _extract_canonical(staging_text: str) -> str:
    start = staging_text.find(CANONICAL_START)
    end = staging_text.find(CANONICAL_END)
    if start < 0 or end < 0 or end <= start:
        raise ConfigurationError("staging document is missing canonical edit markers")
    content = staging_text[start + len(CANONICAL_START) : end].strip()
    if not content:
        raise ConfigurationError("proposed canonical document is empty")
    forbidden = ("secret://", "BEGIN PRIVATE KEY", "/home/", "C:\\Users\\")
    if any(value in content for value in forbidden):
        raise ConfigurationError("proposed canonical document contains forbidden private material")
    return content.rstrip() + "\n"


def _parse_existing(content: str) -> tuple[dict[str, Any], str, list[str]]:
    frontmatter: dict[str, Any] = {}
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end >= 0:
            lines = content[4:end].splitlines()
            current_list: str | None = None
            for line in lines:
                if line.startswith("  - ") and current_list:
                    frontmatter.setdefault(current_list, []).append(json.loads(line[4:]) if line[4:].startswith(('"', "'")) else line[4:])
                    continue
                if ":" not in line or line.startswith(" "):
                    continue
                key, raw = line.split(":", 1)
                raw = raw.strip()
                current_list = None
                if raw == "[]":
                    frontmatter[key] = []
                elif raw == "":
                    frontmatter[key] = []
                    current_list = key
                else:
                    try:
                        frontmatter[key] = json.loads(raw)
                    except json.JSONDecodeError:
                        frontmatter[key] = raw
            body = content[end + 5 :].strip()
        else:
            body = content
    else:
        body = content
    timeline_marker = "\n## Timeline\n"
    if timeline_marker in body:
        before, timeline_text = body.split(timeline_marker, 1)
    else:
        before, timeline_text = body, ""
    lines = before.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    truth = "\n".join(lines).strip()
    if truth.endswith("---"):
        truth = truth[:-3].rstrip()
    timeline = [line for line in timeline_text.splitlines() if line.strip().startswith("-")]
    return frontmatter, truth, timeline


class AutoWiki:
    def __init__(
        self,
        config: BrainConfig,
        store: StateStore,
        *,
        extractor: Extractor | None = None,
    ):
        self.config = config
        self.store = store
        self.extractor = extractor or DeterministicExtractor()

    def capture_inbox(self, event: CaptureEvent) -> Path:
        day = event.occurred_at[:10] if len(event.occurred_at) >= 10 else "unknown"
        relative = Path("01-inbox/captures") / day / f"{event.event_id}.md"
        destination = self.config.resolve_inside(relative)
        if destination.exists():
            return destination
        frontmatter = render_frontmatter(
            {
                "id": event.event_id,
                "type": "capture",
                "status": "observed",
                "connector": event.connector_id,
                "connection": event.connection_id,
                "occurred_at": event.occurred_at,
                "source_record": event.source_record_id,
                "content_hash": event.content_hash,
                "sensitivity": event.visibility,
            }
        )
        actors = ", ".join(actor.display_name for actor in event.actors) or "None declared"
        content = (
            f"{frontmatter}\n\n# Captured {event.kind}\n\n"
            f"**Actors:** {actors}\n\n"
            f"{event.text.strip() or '[No textual body]'}\n\n"
            f"Raw evidence: `event:{event.event_id}`\n"
        )
        atomic_write_text(destination, content, mode=0o600)
        return destination

    def _entity_identity(self, event: CaptureEvent, candidate: EntityCandidate) -> str:
        existing = self.store.get_identity(
            connector_id=event.connector_id,
            connection_id=event.connection_id,
            provider_ref=candidate.provider_ref,
        )
        if existing:
            return existing
        entity_id = "ent_" + uuid5(
            NAMESPACE_URL,
            f"lifeos/entity/{event.connector_id}/{event.connection_id}/{candidate.provider_ref}",
        ).hex
        self.store.put_identity(
            connector_id=event.connector_id,
            connection_id=event.connection_id,
            provider_ref=candidate.provider_ref,
            entity_id=entity_id,
            display_name=candidate.display_name,
        )
        return entity_id

    def _target_path(self, entity_id: str, candidate: EntityCandidate) -> str:
        directory = ENTITY_DIRS.get(candidate.entity_type, "people")
        return f"03-entities/{directory}/{slugify(candidate.display_name)}-{entity_id[-8:]}.md"

    def _render_staging(self, proposal: Proposal, canonical_content: str, *, interaction_count: int) -> str:
        frontmatter = render_frontmatter(
            {
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type,
                "status": proposal.status,
                "target_path": proposal.target_path,
                "target_revision": proposal.target_revision,
                "evidence": list(proposal.evidence_event_ids),
                "created_at": proposal.created_at,
                "interaction_count": interaction_count,
                "sensitivity": "private",
            }
        )
        return (
            f"{frontmatter}\n\n# Review: {proposal.title}\n\n"
            f"{proposal.summary}\n\n"
            "Edit only the canonical document between the markers. Promotion is an explicit owner command.\n\n"
            f"{CANONICAL_START}\n{canonical_content.rstrip()}\n{CANONICAL_END}\n"
        )

    def _propose_entity(self, event: CaptureEvent, candidate: EntityCandidate) -> Proposal:
        entity_id = self._entity_identity(event, candidate)
        target_path = self._target_path(entity_id, candidate)
        target = self.config.resolve_inside(target_path)
        current_revision = file_revision(target)
        active = self.store.find_active_proposal(
            proposal_type="entity_update",
            target_path=target_path,
            connection_id=event.connection_id,
        )
        evidence_ids: list[str]
        interaction_count = 1
        if active:
            old_proposal, old_payload = active
            if old_proposal.target_revision != current_revision:
                self.store.set_proposal_status(old_proposal.proposal_id, "stale")
                active = None
            elif event.event_id in old_proposal.evidence_event_ids:
                return old_proposal
            else:
                evidence_ids = [*old_proposal.evidence_event_ids, event.event_id]
                interaction_count = int(old_payload.get("interaction_count", len(old_proposal.evidence_event_ids))) + 1
        if not active:
            evidence_ids = [event.event_id]

        if target.exists():
            existing_frontmatter, compiled_truth, timeline = _parse_existing(target.read_text(encoding="utf-8"))
            title = str(existing_frontmatter.get("title") or candidate.display_name)
            aliases = list(existing_frontmatter.get("aliases", []))
            sources = list(existing_frontmatter.get("sources", []))
            confidence = str(existing_frontmatter.get("confidence", candidate.confidence))
        else:
            title = candidate.display_name
            aliases = []
            sources = []
            confidence = candidate.confidence
            compiled_truth = (
                f"Captured evidence identifies **{candidate.display_name}** as a "
                f"{candidate.entity_type} in the owner's world. No broader claim has been promoted."
            )
            timeline = []
        source_ref = f"event:{event.event_id}"
        if source_ref not in sources:
            sources.append(source_ref)
        timeline_summary = _excerpt(event.text) or event.kind
        timeline_punctuation = "" if timeline_summary.endswith((".", "!", "?", "…")) else "."
        timeline_entry = (
            f"- {event.occurred_at[:10]}: {timeline_summary}{timeline_punctuation} "
            f"Evidence: `{source_ref}`."
        )
        if timeline_entry not in timeline:
            timeline.append(timeline_entry)
        canonical_content = render_entity_page(
            entity_id=entity_id,
            title=title,
            entity_type=candidate.entity_type,
            aliases=aliases,
            sources=sources,
            compiled_truth=compiled_truth,
            timeline=timeline,
            confidence=confidence,
        )
        proposal_id = active[0].proposal_id if active else "prop_" + uuid4().hex
        staging_rel = (
            active[0].staging_path
            if active
            else f"02-staging/entities/{proposal_id}-{slugify(candidate.display_name)}.md"
        )
        proposal = Proposal(
            proposal_id=proposal_id,
            proposal_type="entity_update",
            status="awaiting_review",
            connector_id=event.connector_id,
            connection_id=event.connection_id,
            target_path=target_path,
            target_revision=current_revision,
            title=f"{candidate.display_name} ({candidate.entity_type})",
            summary=(
                f"{interaction_count} captured interaction(s) support this proposal. "
                "Interaction volume triggers review; it does not prove truth."
            ),
            evidence_event_ids=tuple(evidence_ids),
            staging_path=staging_rel,
            created_at=active[0].created_at if active else utc_now(),
        )
        payload = {
            "entity_id": entity_id,
            "entity_type": candidate.entity_type,
            "provider_ref_private": candidate.provider_ref,
            "canonical_content": canonical_content,
            "interaction_count": interaction_count,
            "extractor_summary": candidate.summary,
        }
        staging = self.config.resolve_inside(staging_rel)
        atomic_write_text(
            staging,
            self._render_staging(proposal, canonical_content, interaction_count=interaction_count),
            mode=0o600,
        )
        self.store.put_proposal(proposal, payload=payload)
        return proposal

    def process_event(self, event: CaptureEvent) -> list[Proposal]:
        self.capture_inbox(event)
        if event.deleted:
            self._stage_deletion(event)
            return []
        candidates = self.extractor.entities(event)
        return [self._propose_entity(event, candidate) for candidate in candidates]

    def _stage_deletion(self, event: CaptureEvent) -> Path:
        relative = f"02-staging/deletions/{event.event_id}.md"
        destination = self.config.resolve_inside(relative)
        if destination.exists():
            return destination
        content = render_frontmatter(
            {
                "status": "awaiting_review",
                "type": "source_deletion",
                "source_event": event.event_id,
                "connector": event.connector_id,
                "sensitivity": "private",
            }
        )
        atomic_write_text(
            destination,
            f"{content}\n\n# Source deletion review\n\nThe provider reported deletion of `{event.source_record_id}`. Canon is unchanged until the owner reviews affected claims.\n",
            mode=0o600,
        )
        return destination

    def promote(self, proposal_id: str, *, reviewer: str) -> PromotionReceipt:
        if not reviewer.strip():
            raise ConfigurationError("reviewer is required")
        proposal, payload = self.store.get_proposal(proposal_id)
        if proposal.status != "awaiting_review":
            raise ConfigurationError(f"proposal is {proposal.status}, not awaiting_review")
        staging = self.config.resolve_inside(proposal.staging_path)
        if not staging.is_file():
            raise ProposalNotFound(f"staging file missing for {proposal_id}")
        canonical_content = _extract_canonical(staging.read_text(encoding="utf-8"))
        target = self.config.resolve_inside(proposal.target_path)
        if file_revision(target) != proposal.target_revision:
            self.store.set_proposal_status(proposal_id, "stale")
            raise StaleProposal(f"canon changed after {proposal_id} was prepared")
        receipt_id = "receipt_" + uuid4().hex
        prepared_path = self.config.receipts_dir / "promotions" / f"{receipt_id}.json"
        prepared_payload = {
            "schema": "lifeos.promotion-receipt/v1",
            "status": "prepared",
            "receipt_id": receipt_id,
            "proposal_id": proposal_id,
            "reviewer": reviewer,
            "target_path": proposal.target_path,
            "before_revision": proposal.target_revision,
            "after_revision_expected": content_digest(canonical_content),
            "evidence_event_ids": list(proposal.evidence_event_ids),
            "prepared_at": utc_now(),
        }
        atomic_write_text(prepared_path, json.dumps(prepared_payload, indent=2, sort_keys=True) + "\n", mode=0o600)
        before, after = write_canonical(
            self.config,
            proposal.target_path,
            canonical_content,
            expected_revision=proposal.target_revision,
        )
        promoted_at = utc_now()
        receipt = PromotionReceipt(
            receipt_id=receipt_id,
            proposal_id=proposal_id,
            reviewer=reviewer,
            target_path=proposal.target_path,
            before_revision=before,
            after_revision=after,
            promoted_at=promoted_at,
            evidence_event_ids=proposal.evidence_event_ids,
        )
        complete_payload = {**receipt.to_dict(), "schema": "lifeos.promotion-receipt/v1", "status": "complete"}
        with self.store.transaction():
            self.store.add_promotion_receipt(receipt, payload=complete_payload)
            self.store.set_proposal_status(proposal_id, "promoted")
        atomic_write_text(prepared_path, json.dumps(complete_payload, indent=2, sort_keys=True) + "\n", mode=0o600)
        archive = self.config.receipts_dir / "proposals" / f"{proposal_id}.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, archive)
        return receipt

    def reject(self, proposal_id: str, *, reviewer: str, reason: str = "") -> Path:
        proposal, _ = self.store.get_proposal(proposal_id)
        if proposal.status != "awaiting_review":
            raise ConfigurationError(f"proposal is {proposal.status}, not awaiting_review")
        source = self.config.resolve_inside(proposal.staging_path)
        archive = self.config.receipts_dir / "rejected" / f"{proposal_id}.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_text(encoding="utf-8") if source.exists() else ""
        note = f"\n\nRejected by {reviewer} at {utc_now()}. Reason: {reason or 'not supplied'}\n"
        atomic_write_text(archive, content + note, mode=0o600)
        source.unlink(missing_ok=True)
        self.store.set_proposal_status(proposal_id, "rejected")
        return archive
