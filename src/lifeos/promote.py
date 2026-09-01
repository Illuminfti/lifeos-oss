"""Owner-gated, atomic canonical promotion transactions."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from typing import Any

from lifeos.canon import (
    CanonError,
    CanonPage,
    CanonicalVault,
    RevisionConflict,
    canonical_claim,
    claim_fingerprint,
    render_body,
    render_markdown,
)
from lifeos.evidence import EvidenceStore
from lifeos.ids import new_id
from lifeos.semantic import Operation, canonical_json, utc_now


class PromotionRequiresOwner(PermissionError):
    pass


class UnresolvedPromotionOperation(CanonError):
    pass


class PromotionService:
    def __init__(self, vault: CanonicalVault, store: EvidenceStore):
        self.vault = vault
        self.store = store
        self.transactions_root = self.vault.root / ".lifeos" / "transactions"
        self.transactions_root.mkdir(parents=True, exist_ok=True)

    def promote_packet(
        self,
        packet_id: str,
        *,
        actor: str,
        owner_confirmed: bool,
        accepted_operation_ids: list[str] | None = None,
        expected_revisions: dict[str, int] | None = None,
    ) -> str:
        if not owner_confirmed:
            raise PromotionRequiresOwner("agents may propose; an explicit owner action must promote")
        packet = self.store.get_review_packet(packet_id)
        if packet is None:
            raise KeyError(packet_id)
        if packet.state != "open":
            raise CanonError(f"review packet is not open: {packet.state}")
        accepted = set(accepted_operation_ids or [item.operation_id for item in packet.operations])
        operations = [item for item in packet.operations if item.operation_id in accepted]
        if not operations:
            raise CanonError("promotion transaction contains no accepted operations")

        transaction_id = new_id("txn")
        expected = dict(expected_revisions or {})
        self.store.record_promotion_transaction(
            transaction_id=transaction_id,
            packet_id=packet_id,
            actor=actor,
            state="preparing",
            expected_revisions=expected,
            operations=[operation.to_dict() for operation in operations],
        )

        pages, candidate_mapping, promoted_claim_ids = self._apply_operations(
            operations,
            actor=actor,
            packet_id=packet_id,
            expected_revisions=expected,
        )
        self._commit_pages(
            transaction_id,
            pages,
            actor=actor,
            packet_id=packet_id,
            expected_revisions=expected,
            operations=operations,
        )

        for candidate_id, subject_id in candidate_mapping.items():
            self.store.mark_candidate_promoted(candidate_id, subject_id)
        for proposed_claim_id in promoted_claim_ids:
            self.store.set_proposed_claim_state(proposed_claim_id, "promoted")
        state = "accepted" if len(accepted) == len(packet.operations) else "partially_accepted"
        self.store.record_review_action(
            packet_id=packet_id,
            action="promote",
            actor=actor,
            details={
                "transaction_id": transaction_id,
                "accepted_operation_ids": sorted(accepted),
            },
            packet_state=state,
        )
        self.store.record_promotion_transaction(
            transaction_id=transaction_id,
            packet_id=packet_id,
            actor=actor,
            state="committed",
            expected_revisions=expected,
            operations=[operation.to_dict() for operation in operations],
            committed_at=utc_now(),
        )
        return transaction_id

    def _apply_operations(
        self,
        operations: list[Operation],
        *,
        actor: str,
        packet_id: str,
        expected_revisions: dict[str, int],
    ) -> tuple[dict[str, CanonPage], dict[str, str], set[str]]:
        pages: dict[str, CanonPage] = {}
        candidate_mapping: dict[str, str] = {}
        promoted_claim_ids: set[str] = set()
        now = utc_now()

        # Allocate every new canonical identity before applying relations.
        for operation in operations:
            if operation.kind != "spawn_subject":
                continue
            payload = operation.payload
            candidate_id = payload["candidate_id"]
            subject_id = payload["suggested_subject_id"]
            candidate_mapping[candidate_id] = subject_id
            if self.vault.load(subject_id) is not None:
                raise CanonError(f"suggested subject id already exists: {subject_id}")
            page = self.vault.new_page(
                subject_id=subject_id,
                type_id=payload["type"],
                kind=payload.get("kind"),
                title=payload["title"],
                sensitivity=payload.get("sensitivity", "private"),
                importance=float(payload.get("importance", 0.5)),
                now=now,
            )
            pages[subject_id] = page
            expected_revisions.setdefault(subject_id, 0)

        def mapped(subject_id: str | None) -> str | None:
            return candidate_mapping.get(subject_id or "", subject_id)

        def page_for(subject_id: str) -> CanonPage:
            subject_id = str(mapped(subject_id))
            if subject_id in pages:
                return pages[subject_id]
            existing = self.vault.load(subject_id)
            if existing is None:
                raise CanonError(f"canonical subject does not exist: {subject_id}")
            expected_revisions.setdefault(subject_id, existing.revision)
            if existing.revision != expected_revisions[subject_id]:
                raise RevisionConflict(
                    f"{subject_id} expected revision {expected_revisions[subject_id]}, "
                    f"found {existing.revision}"
                )
            pages[subject_id] = CanonPage(
                frontmatter=deepcopy(existing.frontmatter),
                body=existing.body,
                path=existing.path,
            )
            return pages[subject_id]

        for operation in operations:
            kind = operation.kind
            if kind == "spawn_subject":
                continue
            if kind == "add_claim":
                subject_id = mapped(operation.subject_id)
                if subject_id is None:
                    raise CanonError("add_claim operation has no subject")
                page = page_for(subject_id)
                proposal_value = deepcopy(operation.payload["claim"])
                self._map_claim_refs(proposal_value, candidate_mapping)
                claim = canonical_claim(
                    proposal_value,
                    owner_actor=actor,
                    proposal_id=operation.payload.get("proposed_claim_id"),
                    promoted_at=now,
                )
                self._add_or_strengthen_claim(page, claim)
                if operation.payload.get("proposed_claim_id"):
                    promoted_claim_ids.add(operation.payload["proposed_claim_id"])
                continue
            if kind == "attach_evidence":
                subject_id = mapped(operation.subject_id)
                if subject_id is None:
                    raise CanonError("attach_evidence operation has no subject")
                page = page_for(subject_id)
                target = self._find_claim(page, operation.payload["claim_id"])
                target["evidence"] = list(
                    dict.fromkeys(target.get("evidence", []) + operation.payload.get("evidence_ids", []))
                )
                for key, value in operation.payload.get("confidence", {}).items():
                    target.setdefault("confidence", {})[key] = max(
                        float(target.get("confidence", {}).get(key, 0)), float(value)
                    )
                continue
            if kind == "supersede_claim":
                subject_id = mapped(operation.subject_id)
                if subject_id is None:
                    raise CanonError("supersede_claim operation has no subject")
                page = page_for(subject_id)
                old = self._find_claim(page, operation.payload["supersedes_claim_id"])
                old["status"] = "superseded"
                old["rank"] = "deprecated"
                proposal_value = deepcopy(operation.payload["claim"])
                self._map_claim_refs(proposal_value, candidate_mapping)
                claim = canonical_claim(
                    proposal_value,
                    owner_actor=actor,
                    proposal_id=operation.payload.get("proposed_claim_id"),
                    promoted_at=now,
                )
                claim["supersedes"] = old["id"]
                self._add_or_strengthen_claim(page, claim)
                if operation.payload.get("proposed_claim_id"):
                    promoted_claim_ids.add(operation.payload["proposed_claim_id"])
                continue
            if kind == "raise_conflict":
                self._apply_conflict(
                    operation,
                    page_for=page_for,
                    mapped=mapped,
                    candidate_mapping=candidate_mapping,
                    actor=actor,
                    packet_id=packet_id,
                    now=now,
                    promoted_claim_ids=promoted_claim_ids,
                )
                continue
            if kind == "create_open_loop":
                self._create_open_loop(
                    operation,
                    pages=pages,
                    candidate_mapping=candidate_mapping,
                    expected_revisions=expected_revisions,
                    actor=actor,
                    packet_id=packet_id,
                    now=now,
                )
                continue
            if kind == "merge_subjects":
                self._merge_subjects(
                    operation,
                    page_for=page_for,
                    mapped=mapped,
                    actor=actor,
                    now=now,
                )
                continue
            if kind == "resolve_identity":
                # An identity choice is operational unless it includes an
                # explicit canonical merge or candidate promotion operation.
                if not operation.payload.get("chosen_subject_id"):
                    raise UnresolvedPromotionOperation(
                        "identity packet requires chosen_subject_id before promotion"
                    )
                continue
            if kind in {"create_event", "create_decision", "update_open_loop"}:
                self._create_occurrence_or_loop(
                    operation,
                    pages=pages,
                    expected_revisions=expected_revisions,
                    actor=actor,
                    packet_id=packet_id,
                    now=now,
                )
                continue
            raise UnresolvedPromotionOperation(f"unsupported promotion operation: {kind}")

        # One revision increment per modified pre-existing page.
        for subject_id, page in pages.items():
            initial = expected_revisions.get(subject_id, 0)
            page.frontmatter["revision"] = 1 if initial == 0 else initial + 1
            page.frontmatter["updated_at"] = now
            page.frontmatter.setdefault("review", {})["last_owner_reviewed_at"] = now
            page.body = render_body(page.frontmatter)
            self.vault.validator.validate_page(page.frontmatter)
        return pages, candidate_mapping, promoted_claim_ids

    @staticmethod
    def _map_claim_refs(value: dict[str, Any], mapping: dict[str, str]) -> None:
        subject_id = value.get("subject_id")
        if subject_id in mapping:
            value["subject_id"] = mapping[subject_id]
        object_value = value.get("object", {})
        if object_value.get("ref") in mapping:
            object_value["ref"] = mapping[object_value["ref"]]

    @staticmethod
    def _find_claim(page: CanonPage, claim_id: str) -> dict[str, Any]:
        for claim in page.frontmatter.get("claims", []):
            if claim.get("id") == claim_id:
                return claim
        raise CanonError(f"claim {claim_id} not found on {page.id}")

    @staticmethod
    def _add_or_strengthen_claim(page: CanonPage, claim: dict[str, Any]) -> None:
        fingerprint = claim_fingerprint(claim, page.id)
        for existing in page.frontmatter.get("claims", []):
            if claim_fingerprint(existing, page.id) == fingerprint:
                existing["evidence"] = list(
                    dict.fromkeys(existing.get("evidence", []) + claim.get("evidence", []))
                )
                for key, value in claim.get("confidence", {}).items():
                    existing.setdefault("confidence", {})[key] = max(
                        float(existing.get("confidence", {}).get(key, 0)), float(value)
                    )
                return
        page.frontmatter.setdefault("claims", []).append(claim)

    def _apply_conflict(
        self,
        operation: Operation,
        *,
        page_for: Any,
        mapped: Any,
        candidate_mapping: dict[str, str],
        actor: str,
        packet_id: str,
        now: str,
        promoted_claim_ids: set[str],
    ) -> None:
        resolution = operation.payload.get("resolution")
        if resolution not in {"keep_existing", "accept_proposed", "record_disputed"}:
            raise UnresolvedPromotionOperation(
                "conflict operation needs resolution: keep_existing, accept_proposed, or record_disputed"
            )
        if resolution == "keep_existing":
            return
        subject_id = mapped(operation.subject_id)
        page = page_for(subject_id)
        existing = self._find_claim(page, operation.payload["existing_claim_id"])
        proposal_value = deepcopy(operation.payload["proposed_claim"])
        self._map_claim_refs(proposal_value, candidate_mapping)
        proposed = canonical_claim(
            proposal_value,
            owner_actor=actor,
            proposal_id=operation.payload.get("proposed_claim_id"),
            promoted_at=now,
        )
        if resolution == "accept_proposed":
            existing["status"] = "superseded"
            existing["rank"] = "deprecated"
            proposed["supersedes"] = existing["id"]
        else:
            existing["status"] = "disputed"
            proposed["status"] = "disputed"
            proposed["modality"] = "disputed"
        self._add_or_strengthen_claim(page, proposed)
        if operation.payload.get("proposed_claim_id"):
            promoted_claim_ids.add(operation.payload["proposed_claim_id"])

    def _create_open_loop(
        self,
        operation: Operation,
        *,
        pages: dict[str, CanonPage],
        candidate_mapping: dict[str, str],
        expected_revisions: dict[str, int],
        actor: str,
        packet_id: str,
        now: str,
    ) -> None:
        payload = operation.payload
        subject_id = payload.get("open_loop_id") or new_id("lop")
        page = self.vault.new_page(
            subject_id=subject_id,
            type_id="open_loop",
            kind=payload.get("kind", "task"),
            title=payload["title"],
            sensitivity=payload.get("sensitivity", "private"),
            importance=float(payload.get("importance", operation.priority)),
            now=now,
        )
        expected_revisions[subject_id] = 0
        for proposal_value in payload.get("claims", []):
            proposal_value = deepcopy(proposal_value)
            proposal_value.setdefault("subject_id", subject_id)
            proposal_value.setdefault("subject_type", "open_loop")
            proposal_value.setdefault("evidence_ids", operation.evidence_ids)
            self._map_claim_refs(proposal_value, candidate_mapping)
            claim = canonical_claim(
                proposal_value,
                owner_actor=actor,
                proposal_id=packet_id,
                promoted_at=now,
            )
            self._add_or_strengthen_claim(page, claim)
        pages[subject_id] = page

    def _create_occurrence_or_loop(
        self,
        operation: Operation,
        *,
        pages: dict[str, CanonPage],
        expected_revisions: dict[str, int],
        actor: str,
        packet_id: str,
        now: str,
    ) -> None:
        type_id = {
            "create_event": "event",
            "create_decision": "decision",
            "update_open_loop": "open_loop",
        }[operation.kind]
        payload = operation.payload
        subject_id = payload.get("id") or new_id(self.vault.ontology.types[type_id].id_prefix)
        existing = self.vault.load(subject_id)
        if existing:
            pages[subject_id] = CanonPage(deepcopy(existing.frontmatter), existing.body, existing.path)
            expected_revisions.setdefault(subject_id, existing.revision)
            page = pages[subject_id]
        else:
            page = self.vault.new_page(
                subject_id=subject_id,
                type_id=type_id,
                kind=payload.get("kind"),
                title=payload["title"],
                sensitivity=payload.get("sensitivity", "private"),
                importance=float(payload.get("importance", operation.priority)),
                now=now,
            )
            expected_revisions[subject_id] = 0
            pages[subject_id] = page
        for proposal_value in payload.get("claims", []):
            proposal_value = deepcopy(proposal_value)
            proposal_value.setdefault("subject_id", subject_id)
            proposal_value.setdefault("subject_type", type_id)
            proposal_value.setdefault("evidence_ids", operation.evidence_ids)
            claim = canonical_claim(
                proposal_value,
                owner_actor=actor,
                proposal_id=packet_id,
                promoted_at=now,
            )
            self._add_or_strengthen_claim(page, claim)

    def _merge_subjects(
        self,
        operation: Operation,
        *,
        page_for: Any,
        mapped: Any,
        actor: str,
        now: str,
    ) -> None:
        source_id = mapped(operation.payload["source_id"])
        target_id = mapped(operation.payload["target_id"])
        if source_id == target_id:
            return
        source = page_for(source_id)
        target = page_for(target_id)
        if source.frontmatter["type"] != target.frontmatter["type"]:
            raise CanonError("canonical merge cannot cross root types")
        for claim in source.frontmatter.get("claims", []):
            self._add_or_strengthen_claim(target, deepcopy(claim))
        aliases = target.frontmatter.setdefault("aliases", [])
        for alias in [source.frontmatter["title"], *source.frontmatter.get("aliases", [])]:
            if alias != target.frontmatter["title"] and alias not in aliases:
                aliases.append(alias)
        source.frontmatter["status"] = "merged"
        source.frontmatter["redirect_to"] = target_id
        source.frontmatter["merged_at"] = now
        source.frontmatter["merge_actor"] = actor

    def _commit_pages(
        self,
        transaction_id: str,
        pages: dict[str, CanonPage],
        *,
        actor: str,
        packet_id: str,
        expected_revisions: dict[str, int],
        operations: list[Operation],
    ) -> None:
        tx_dir = self.transactions_root / transaction_id
        after_dir = tx_dir / "after"
        after_dir.mkdir(parents=True, exist_ok=False)
        manifest = self.vault._read_manifest()
        entries: list[dict[str, Any]] = []
        for subject_id, page in sorted(pages.items()):
            destination = page.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            relative = destination.relative_to(self.vault.root).as_posix()
            after_path = after_dir / f"{subject_id}.md"
            text = render_markdown(page.frontmatter, page.body)
            after_path.write_text(text, encoding="utf-8")
            before_hash = None
            if destination.exists():
                before_hash = sha256(destination.read_bytes()).hexdigest()
            entries.append(
                {
                    "subject_id": subject_id,
                    "destination": relative,
                    "after": after_path.relative_to(tx_dir).as_posix(),
                    "before_hash": before_hash,
                    "after_hash": sha256(text.encode("utf-8")).hexdigest(),
                    "expected_revision": expected_revisions.get(subject_id, 0),
                }
            )
            manifest[subject_id] = relative

        journal = {
            "transaction_id": transaction_id,
            "state": "prepared",
            "actor": actor,
            "packet_id": packet_id,
            "created_at": utc_now(),
            "expected_revisions": expected_revisions,
            "operations": [operation.to_dict() for operation in operations],
            "pages": entries,
        }
        journal_path = tx_dir / "journal.json"
        journal_path.write_text(json.dumps(journal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._apply_prepared_transaction(tx_dir, journal)
        self.vault._write_manifest(manifest)
        journal["state"] = "committed"
        journal["committed_at"] = utc_now()
        journal_path.write_text(json.dumps(journal, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _apply_prepared_transaction(self, tx_dir: Path, journal: dict[str, Any]) -> None:
        for entry in journal["pages"]:
            source = tx_dir / entry["after"]
            destination = self.vault.root / entry["destination"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.with_suffix(destination.suffix + ".lifeos-tmp")
            shutil.copyfile(source, temp)
            os.replace(temp, destination)

    def recover_prepared_transactions(self) -> list[str]:
        recovered: list[str] = []
        for journal_path in sorted(self.transactions_root.glob("*/journal.json")):
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if journal.get("state") != "prepared":
                continue
            tx_dir = journal_path.parent
            self._apply_prepared_transaction(tx_dir, journal)
            manifest = self.vault._read_manifest()
            for entry in journal["pages"]:
                manifest[entry["subject_id"]] = entry["destination"]
            self.vault._write_manifest(manifest)
            journal["state"] = "committed"
            journal["recovered_at"] = utc_now()
            journal_path.write_text(
                json.dumps(journal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            recovered.append(journal["transaction_id"])
        return recovered
