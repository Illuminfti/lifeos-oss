"""Canonical Markdown tree and safe file operations."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from lifeos.config import BrainConfig, atomic_write_text, deep_merge
from lifeos.contracts import content_digest
from lifeos.errors import ConfigurationError, UnsafePath

TREE = (
    "CANON.md",
    "SCHEMA.md",
    "README.md",
    ".gitattributes",
    ".gitignore",
    "00-dashboards/now.md",
    "00-dashboards/review.md",
    "00-dashboards/connectors.md",
    "00-dashboards/health.md",
    "01-inbox/captures/.gitkeep",
    "01-inbox/tasks/.gitkeep",
    "02-staging/entities/.gitkeep",
    "02-staging/facts/.gitkeep",
    "02-staging/relations/.gitkeep",
    "02-staging/conflicts/.gitkeep",
    "02-staging/deletions/.gitkeep",
    "03-entities/people/.gitkeep",
    "03-entities/organizations/.gitkeep",
    "03-entities/places/.gitkeep",
    "03-entities/tools/.gitkeep",
    "03-entities/assets/.gitkeep",
    "03-entities/media/.gitkeep",
    "04-work/projects/.gitkeep",
    "04-work/areas/.gitkeep",
    "04-work/campaigns/.gitkeep",
    "05-knowledge/concepts/.gitkeep",
    "05-knowledge/theses/.gitkeep",
    "05-knowledge/comparisons/.gitkeep",
    "05-knowledge/maps/.gitkeep",
    "06-execution/decisions/.gitkeep",
    "06-execution/playbooks/.gitkeep",
    "06-execution/checklists/.gitkeep",
    "06-execution/templates/.gitkeep",
    "07-raw/.gitkeep",
    "99-archive/.gitkeep",
    ".lifeos/.gitignore",
)

CANONICAL_PREFIXES = (
    "00-dashboards/",
    "03-entities/",
    "04-work/",
    "05-knowledge/",
    "06-execution/",
)

CANON_TEXT = """---
title: LifeOS Canon
type: dashboard
status: canonical
confidence: high
sensitivity: private
sources: []
---

# Canon

This directory is the sole canonical memory home for this LifeOS brain.

Raw evidence is not canon. Staging proposals are not canon. GBrain and pgGraph
are derived and rebuildable. The LifeOS Intelligence Kernel is read-only.

Only an explicit owner promotion may write canonical entity, work, knowledge,
or execution pages.
"""

SCHEMA_TEXT = """# LifeOS Markdown schema

Canonical pages require YAML frontmatter with: `id`, `title`, `type`, `status`,
`aliases`, `sources`, `confidence`, `reviewed`, `next_review`, and
`sensitivity`.

The body uses compiled truth followed by an append-only timeline. Operational
state below `.lifeos/` is private, ignored by Git, and excluded from retrieval.
"""

README_TEXT = """# LifeOS brain

Human-readable canonical Markdown. Read `CANON.md` before changing files.

- `01-inbox/` is cheap capture.
- `02-staging/` is reviewable machine work, never canon.
- `03-entities/` through `06-execution/` are canonical after owner promotion.
- `07-raw/` preserves source evidence.
- `.lifeos/` holds private operational state and is not knowledge.
"""

DASHBOARDS = {
    "00-dashboards/now.md": "# Now\n\nNo compiled state yet.\n",
    "00-dashboards/review.md": "# Review\n\nUse `lifeos staging list` for pending proposals.\n",
    "00-dashboards/connectors.md": "# Connectors\n\nUse `lifeos connector list --connections`.\n",
    "00-dashboards/health.md": "# Health\n\nUse `lifeos doctor --json`.\n",
}


def file_revision(path: Path) -> str:
    if not path.exists():
        return "missing"
    if not path.is_file():
        raise UnsafePath(f"not a file: {path}")
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def slugify(value: str, *, fallback: str = "entity") -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return (normalized or fallback)[:80]


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def render_frontmatter(values: Mapping[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {yaml_scalar(item)}")
        elif isinstance(value, Mapping):
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def render_entity_page(
    *,
    entity_id: str,
    title: str,
    entity_type: str,
    sources: Iterable[str],
    compiled_truth: str,
    timeline: Iterable[str],
    aliases: Iterable[str] = (),
    sensitivity: str = "private",
    confidence: str = "medium",
) -> str:
    today = date.today()
    frontmatter = render_frontmatter(
        {
            "id": entity_id,
            "title": title,
            "type": entity_type,
            "status": "canonical",
            "aliases": list(dict.fromkeys(aliases)),
            "sources": list(dict.fromkeys(sources)),
            "confidence": confidence,
            "reviewed": today.isoformat(),
            "next_review": (today + timedelta(days=90)).isoformat(),
            "sensitivity": sensitivity,
        }
    )
    truth = compiled_truth.strip() or "No reviewed compiled truth yet."
    entries = [entry.rstrip() for entry in timeline if entry.strip()]
    timeline_text = "\n".join(entries) if entries else "- No reviewed timeline entries yet."
    return f"{frontmatter}\n\n# {title}\n\n{truth}\n\n---\n\n## Timeline\n\n{timeline_text}\n"


def canonical_path(config: BrainConfig, relative: str | Path) -> Path:
    text = Path(relative).as_posix().lstrip("/")
    if text in {"CANON.md", "SCHEMA.md"}:
        return config.resolve_inside(text)
    if not any(text.startswith(prefix) for prefix in CANONICAL_PREFIXES):
        raise UnsafePath(f"not a canonical path: {text}")
    return config.resolve_inside(text)


def read_page(config: BrainConfig, relative: str | Path) -> str:
    path = config.resolve_inside(relative)
    if not path.is_file():
        raise FileNotFoundError(str(relative))
    return path.read_text(encoding="utf-8")


def write_canonical(
    config: BrainConfig,
    relative: str | Path,
    content: str,
    *,
    expected_revision: str,
) -> tuple[str, str]:
    path = canonical_path(config, relative)
    before = file_revision(path)
    if before != expected_revision:
        from lifeos.errors import StaleProposal

        raise StaleProposal(
            f"{relative} changed after proposal: expected {expected_revision}, found {before}"
        )
    if not content.startswith("---\n"):
        raise ConfigurationError("canonical Markdown must begin with YAML frontmatter")
    if "status: \"canonical\"" not in content and "status: canonical" not in content:
        raise ConfigurationError("canonical Markdown must declare status: canonical")
    atomic_write_text(path, content.rstrip() + "\n", mode=0o600)
    return before, file_revision(path)


def init_brain(path: Path, *, overrides: Mapping[str, Any] | None = None) -> BrainConfig:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    content_map = {
        "CANON.md": CANON_TEXT,
        "SCHEMA.md": SCHEMA_TEXT,
        "README.md": README_TEXT,
        ".gitattributes": "*.md text eol=lf\n*.json text eol=lf\n",
        ".gitignore": ".lifeos/\n",
        ".lifeos/.gitignore": "*\n!.gitignore\n",
        **DASHBOARDS,
    }
    for relative in TREE:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            continue
        if relative.endswith(".gitkeep"):
            destination.write_text("", encoding="utf-8")
        else:
            destination.write_text(content_map.get(relative, ""), encoding="utf-8")
    state_dir = root / ".lifeos"
    state_dir.mkdir(exist_ok=True)
    try:
        os.chmod(state_dir, 0o700)
    except OSError:
        pass
    values = deep_merge(
        {
            "schema": "lifeos.config/v1",
            "brain_id": "brain_" + content_digest(str(root))[-12:],
            "gbrain": {"command": "gbrain", "enabled": True},
            "kernel": {"max_chars": 4000, "max_results": 8},
            "ingest": {"max_attempts": 5, "lease_seconds": 60},
            "models": {"extractor": "deterministic"},
        },
        overrides or {},
    )
    config = BrainConfig(root=root, values=values)
    if not config.config_path.exists():
        config.save()
    return config
