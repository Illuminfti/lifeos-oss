"""Local-only migration from the legacy folder ontology.

The scanner is read-only. Redacted reports contain counts and hashes, never
page titles, body text, identifiers, snippets, or source paths.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import yaml

from lifeos.canon import parse_markdown
from lifeos.semantic import utc_now

LEGACY_FOLDER_MAP: dict[str, str | None] = {
    "people": "person",
    "companies": "organization",
    "organizations": "organization",
    "protocols": None,
    "tools": "asset",
    "assets": "asset",
    "media": None,
    "intel": "concept",
    "topics": "concept",
    "workstreams": "project",
    "relationships": None,
    "projects": "project",
    "areas": "life_function",
    "campaigns": "project",
    "concepts": "concept",
    "theses": "concept",
    "comparisons": None,
    "maps": None,
    "decisions": "decision",
    "logs": None,
}

LEGACY_TYPE_MAP: dict[str, str | None] = {
    "entity": None,
    "project": "project",
    "area": "life_function",
    "concept": "concept",
    "thesis": "concept",
    "decision": "decision",
    "playbook": None,
    "checklist": None,
    "dashboard": None,
    "map": None,
    "comparison": None,
    "log": None,
    "archive": None,
    "inbox": None,
}


@dataclass(slots=True)
class MigrationPage:
    path: Path
    path_hash: str
    content_hash: str
    top_folder: str
    legacy_type: str | None
    mapped_type: str | None
    disposition: str
    issues: list[str]

    def redacted(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("path")
        return value


class LegacyVaultScanner:
    VERSION = "migration-scan/v2"

    def __init__(self, vault_path: Path, *, salt: str = "lifeos-local-migration"):
        self.vault_path = Path(vault_path).resolve()
        self.salt = salt

    def scan(self) -> list[MigrationPage]:
        pages: list[MigrationPage] = []
        for path in sorted(self.vault_path.rglob("*.md")):
            if ".lifeos" in path.parts or ".git" in path.parts:
                continue
            relative = path.relative_to(self.vault_path)
            top = relative.parts[0].casefold() if len(relative.parts) > 1 else "root"
            semantic_segments = [
                part.casefold().split("-", 1)[1] if part[:2].isdigit() and "-" in part else part.casefold()
                for part in relative.parts[:-1]
            ]
            text = path.read_text(encoding="utf-8", errors="replace")
            content_hash = sha256(text.encode("utf-8")).hexdigest()
            path_hash = sha256(f"{self.salt}:{relative.as_posix()}".encode("utf-8")).hexdigest()
            issues: list[str] = []
            frontmatter: dict[str, Any] = {}
            try:
                frontmatter, _body = parse_markdown(text)
            except Exception:
                issues.append("missing_or_invalid_frontmatter")
                if text.startswith("---"):
                    try:
                        raw = text.split("---", 2)[1]
                        value = yaml.safe_load(raw)
                        if isinstance(value, dict):
                            frontmatter = value
                    except Exception:
                        pass
            legacy_type = str(frontmatter.get("type")) if frontmatter.get("type") else None
            mapped_type = self._map_type(semantic_segments, legacy_type)
            disposition = "candidate_canon"
            if top in {"00-dashboards", "01-inbox", "02-staging", "07-raw"}:
                disposition = "noncanonical"
            elif top in {"05-knowledge"} and legacy_type in {"comparison", "map"}:
                disposition = "artifact"
            elif top in {"06-execution"} and legacy_type in {"playbook", "checklist"}:
                disposition = "artifact"
            elif mapped_type is None:
                disposition = "needs_local_classification"
                issues.append("ambiguous_legacy_class")
            if legacy_type in {"entity", "relationship", "log"}:
                issues.append("mixed_dimension_type")
            pages.append(
                MigrationPage(
                    path=path,
                    path_hash=path_hash,
                    content_hash=content_hash,
                    top_folder=top,
                    legacy_type=legacy_type,
                    mapped_type=mapped_type,
                    disposition=disposition,
                    issues=issues,
                )
            )
        return pages

    @staticmethod
    def _map_type(segments: list[str], legacy_type: str | None) -> str | None:
        for segment in reversed(segments):
            if segment in LEGACY_FOLDER_MAP:
                folder_value = LEGACY_FOLDER_MAP[segment]
                if folder_value is not None:
                    return folder_value
        if legacy_type in LEGACY_TYPE_MAP:
            return LEGACY_TYPE_MAP[legacy_type]
        return None

    def redacted_report(self) -> dict[str, Any]:
        pages = self.scan()
        folders = Counter(page.top_folder for page in pages)
        legacy_types = Counter(page.legacy_type or "missing" for page in pages)
        dispositions = Counter(page.disposition for page in pages)
        issues = Counter(issue for page in pages for issue in page.issues)
        return {
            "schema": "lifeos.migration-report/v2",
            "scanner_version": self.VERSION,
            "generated_at": utc_now(),
            "privacy": {
                "redacted": True,
                "contains_titles": False,
                "contains_body_text": False,
                "contains_source_paths": False,
                "contains_identifiers": False,
            },
            "counts": {
                "pages": len(pages),
                "folders": dict(sorted(folders.items())),
                "legacy_types": dict(sorted(legacy_types.items())),
                "dispositions": dict(sorted(dispositions.items())),
                "issues": dict(sorted(issues.items())),
            },
            "pages": [page.redacted() for page in pages],
        }

    def write_redacted_report(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.redacted_report(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return destination


class MigrationPlanner:
    """Build a local plan. It does not copy private pages into source control."""

    VERSION = "migration-plan/v2"

    def __init__(self, scanner: LegacyVaultScanner):
        self.scanner = scanner

    def plan(self, *, include_private_paths: bool = False) -> dict[str, Any]:
        pages = self.scanner.scan()
        items: list[dict[str, Any]] = []
        for page in pages:
            item = page.redacted()
            if include_private_paths:
                item["source_path"] = str(page.path)
            item["action"] = self._action(page)
            items.append(item)
        return {
            "schema": "lifeos.migration-plan/v2",
            "planner_version": self.VERSION,
            "generated_at": utc_now(),
            "local_only": True,
            "items": items,
        }

    @staticmethod
    def _action(page: MigrationPage) -> str:
        if page.disposition == "noncanonical":
            return "retain_as_evidence_or_workflow_state"
        if page.disposition == "artifact":
            return "move_to_04_artifacts"
        if page.disposition == "needs_local_classification":
            return "create_review_packet"
        return "parse_into_candidate_claims_then_owner_promote"
