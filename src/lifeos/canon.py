"""Canonical Markdown pages and schema validation.

Markdown remains the sole canonical memory home. Structured frontmatter holds
atomic claims; the prose body is a human rendering, never an independent fact
source.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator

from jsonschema import Draft202012Validator
import yaml

from lifeos.ids import new_id
from lifeos.ontology import Ontology
from lifeos.semantic import ProposedClaim, canonical_json, utc_now

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class CanonError(RuntimeError):
    pass


class RevisionConflict(CanonError):
    pass


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:80] or "untitled"


def claim_fingerprint(claim: dict[str, Any], subject_id: str) -> str:
    qualifiers = claim.get("qualifiers", {})
    material = {
        "subject_id": subject_id,
        "predicate": claim["predicate"],
        "object": claim["object"],
        "polarity": claim.get("polarity", "positive"),
        "modality": claim.get("modality", "actual"),
        "validity": {
            key: qualifiers.get(key)
            for key in ("valid_from", "valid_to")
            if key in qualifiers
        },
    }
    return sha256(canonical_json(material).encode("utf-8")).hexdigest()


class CanonValidator:
    def __init__(self, ontology: Ontology | None = None):
        self.ontology = ontology or Ontology.default()
        package = resources.files("lifeos.resources").joinpath("json-schema")
        self.page_schema = json.loads(package.joinpath("canon-page-v2.json").read_text())
        self.claim_schema = json.loads(package.joinpath("claim-v2.json").read_text())
        self.page_validator = Draft202012Validator(self.page_schema)
        self.claim_validator = Draft202012Validator(self.claim_schema)

    def validate_page(self, frontmatter: dict[str, Any]) -> None:
        errors = sorted(self.page_validator.iter_errors(frontmatter), key=lambda e: list(e.path))
        if errors:
            raise CanonError("invalid canonical page: " + "; ".join(error.message for error in errors))
        page_type = str(frontmatter["type"])
        self.ontology.validate_type(page_type, frontmatter.get("kind"))
        seen_claim_ids: set[str] = set()
        for claim in frontmatter.get("claims", []):
            claim_errors = sorted(self.claim_validator.iter_errors(claim), key=lambda e: list(e.path))
            if claim_errors:
                raise CanonError(
                    f"invalid claim {claim.get('id')}: "
                    + "; ".join(error.message for error in claim_errors)
                )
            if claim["id"] in seen_claim_ids:
                raise CanonError(f"duplicate claim id on page: {claim['id']}")
            seen_claim_ids.add(claim["id"])
            object_value = claim["object"]
            object_kind = "entity_ref" if "ref" in object_value else "literal"
            object_type = object_value.get("type") if object_kind == "entity_ref" else object_value.get("datatype")
            if object_value.get("state") in {"unknown", "no_value"}:
                object_type = self.ontology.predicates[claim["predicate"]].range[0]
            self.ontology.validate_claim(
                predicate_id=claim["predicate"],
                subject_type=page_type,
                object_kind=object_kind,
                object_type=object_type,
                qualifiers=claim.get("qualifiers", {}),
            )


def parse_markdown(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER.match(text)
    if not match:
        raise CanonError("canonical Markdown must begin with YAML frontmatter")
    frontmatter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(frontmatter, dict):
        raise CanonError("frontmatter must be a mapping")
    return frontmatter, text[match.end() :]


def render_body(frontmatter: dict[str, Any]) -> str:
    title = frontmatter["title"]
    active_count = sum(
        1 for claim in frontmatter.get("claims", []) if claim.get("status", "active") == "active"
    )
    redirect = frontmatter.get("redirect_to")
    if redirect:
        return (
            f"# {title}\n\n"
            f"> This canonical subject was merged into `{redirect}`. The redirect is reversible.\n"
        )
    return (
        f"# {title}\n\n"
        "## Current picture\n\n"
        "<!-- Generated from structured canonical claims. Do not parse this prose as evidence. -->\n\n"
        f"{active_count} active canonical claim(s). Query the structured claim records for truth.\n\n"
        "## History\n\n"
        "Historical, disputed, superseded, and retracted claims remain in frontmatter with provenance.\n"
    )


def render_markdown(frontmatter: dict[str, Any], body: str | None = None) -> str:
    dumped = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    ).rstrip()
    return f"---\n{dumped}\n---\n\n{body if body is not None else render_body(frontmatter)}"


def canonical_claim(
    proposal: ProposedClaim | dict[str, Any],
    *,
    owner_actor: str,
    proposal_id: str | None,
    promoted_at: str | None = None,
) -> dict[str, Any]:
    value = proposal.to_dict() if isinstance(proposal, ProposedClaim) else dict(proposal)
    promoted_at = promoted_at or utc_now()
    claim = {
        "id": new_id("clm"),
        "predicate": value["predicate"],
        "object": value["object"],
        "polarity": value.get("polarity", "positive"),
        "modality": value.get("modality", "actual"),
        "qualifiers": value.get("qualifiers", {}),
        "status": "active",
        "rank": "normal",
        "confidence": value.get("confidence", {}),
        "evidence": list(dict.fromkeys(value.get("evidence_ids", []))),
        "asserted_at": value.get("asserted_at"),
        "recorded_at": promoted_at,
        "review": {
            "state": "owner_promoted",
            "actor": owner_actor,
            "proposal_id": proposal_id,
            "promoted_at": promoted_at,
        },
        "supersedes": value.get("supersedes"),
        "sensitivity": value.get("sensitivity", "personal"),
    }
    return claim


@dataclass(slots=True)
class CanonPage:
    frontmatter: dict[str, Any]
    body: str
    path: Path

    @property
    def id(self) -> str:
        return str(self.frontmatter["id"])

    @property
    def revision(self) -> int:
        return int(self.frontmatter["revision"])


class CanonicalVault:
    def __init__(self, root: Path, *, ontology: Ontology | None = None):
        self.root = Path(root).resolve()
        self.ontology = ontology or Ontology.default()
        self.validator = CanonValidator(self.ontology)
        self.manifest_path = self.root / ".lifeos" / "manifest.json"
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            self._write_manifest({})

    def _read_manifest(self) -> dict[str, str]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return {str(key): str(path) for key, path in value.items()}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_manifest(self, manifest: dict[str, str]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.manifest_path.with_suffix(".tmp")
        temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, self.manifest_path)

    def path_for(self, subject_id: str) -> Path | None:
        relative = self._read_manifest().get(subject_id)
        return self.root / relative if relative else None

    def suggest_path(self, *, subject_id: str, type_id: str, title: str, created_at: str) -> Path:
        self.ontology.validate_type(type_id)
        base = self.root / "03-canon" / type_id.replace("_", "-")
        if type_id in {"event", "decision"}:
            year = created_at[:4] if len(created_at) >= 4 and created_at[:4].isdigit() else "unknown"
            base = base / year
        return base / f"{slugify(title)}--{subject_id}.md"

    def load(self, subject_id: str) -> CanonPage | None:
        path = self.path_for(subject_id)
        if path is None or not path.exists():
            return None
        frontmatter, body = parse_markdown(path.read_text(encoding="utf-8"))
        self.validator.validate_page(frontmatter)
        return CanonPage(frontmatter=frontmatter, body=body, path=path)

    def iter_pages(self) -> Iterator[CanonPage]:
        canon_root = self.root / "03-canon"
        if not canon_root.exists():
            return
        for path in sorted(canon_root.rglob("*.md")):
            frontmatter, body = parse_markdown(path.read_text(encoding="utf-8"))
            self.validator.validate_page(frontmatter)
            yield CanonPage(frontmatter=frontmatter, body=body, path=path)

    def new_page(
        self,
        *,
        subject_id: str,
        type_id: str,
        title: str,
        kind: str | None = None,
        sensitivity: str = "private",
        importance: float = 0.5,
        now: str | None = None,
    ) -> CanonPage:
        self.ontology.validate_type(type_id, kind)
        now = now or utc_now()
        frontmatter = {
            "schema": "lifeos.canon-page/v2",
            "ontology_version": self.ontology.version,
            "id": subject_id,
            "type": type_id,
            "kind": kind,
            "title": title,
            "status": "active",
            "aliases": [],
            "sensitivity": sensitivity,
            "importance": max(0.0, min(1.0, float(importance))),
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "review": {"last_owner_reviewed_at": now, "next_review_at": None},
            "claims": [],
        }
        path = self.suggest_path(
            subject_id=subject_id, type_id=type_id, title=title, created_at=now
        )
        self.validator.validate_page(frontmatter)
        return CanonPage(frontmatter=frontmatter, body=render_body(frontmatter), path=path)

    def page_hash(self, page: CanonPage) -> str:
        return sha256(render_markdown(page.frontmatter, page.body).encode("utf-8")).hexdigest()

    def revision_hash(self) -> str:
        material = []
        for page in self.iter_pages():
            material.append((page.id, page.revision, self.page_hash(page)))
        return sha256(canonical_json(material).encode("utf-8")).hexdigest()
