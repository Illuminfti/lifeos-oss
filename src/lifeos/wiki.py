"""Canonical Markdown tree and safe atomic file operations."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator

TREE = [
    "CANON.md", "SCHEMA.md", "README.md",
    "00-dashboards/now.md", "00-dashboards/review.md", "00-dashboards/connectors.md", "00-dashboards/health.md",
    "01-inbox/captures/.gitkeep", "01-inbox/tasks/.gitkeep",
    "02-staging/entities/.gitkeep", "02-staging/facts/.gitkeep", "02-staging/relations/.gitkeep",
    "02-staging/conflicts/.gitkeep", "02-staging/deletions/.gitkeep",
    "03-entities/people/.gitkeep", "03-entities/organizations/.gitkeep", "03-entities/places/.gitkeep",
    "03-entities/tools/.gitkeep", "03-entities/assets/.gitkeep", "03-entities/media/.gitkeep",
    "04-work/projects/.gitkeep", "04-work/areas/.gitkeep", "04-work/campaigns/.gitkeep",
    "05-knowledge/concepts/.gitkeep", "05-knowledge/theses/.gitkeep", "05-knowledge/comparisons/.gitkeep",
    "05-knowledge/maps/.gitkeep", "06-execution/decisions/.gitkeep", "06-execution/playbooks/.gitkeep",
    "06-execution/checklists/.gitkeep", "06-execution/templates/.gitkeep",
    "07-raw/.gitkeep", "99-archive/.gitkeep", ".lifeos/.gitignore",
]

CANON = """# Canon

This directory is the sole canonical memory home.

GBrain and pgGraph are derived. Staging and raw evidence are not canon. Agents write proposals under 02-staging. Only explicit owner promotion writes canonical pages.
"""

SCHEMA = """# Schema

Canonical pages use frontmatter: id, title, type, status, aliases, sources, confidence, reviewed, next_review, sensitivity.

Operational state lives under `.lifeos/` and is not indexed as canon.
"""


def init_brain(path: Path) -> Path:
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for rel in TREE:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            continue
        if rel == "CANON.md":
            dest.write_text(CANON, encoding="utf-8")
        elif rel == "SCHEMA.md":
            dest.write_text(SCHEMA, encoding="utf-8")
        elif rel == "README.md":
            dest.write_text("# LifeOS brain\n\nCanonical Markdown. See CANON.md.\n", encoding="utf-8")
        elif rel == ".lifeos/.gitignore":
            dest.write_text("*\n!.gitignore\n", encoding="utf-8")
        else:
            dest.write_text("", encoding="utf-8")
    return root


def safe_path(root: Path, relative: str) -> Path:
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if base != target and base not in target.parents:
        raise ValueError("path escapes LifeOS brain")
    return target


def revision(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"


def render_frontmatter(values: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, (list, dict)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif value is None:
            rendered = "null"
        elif isinstance(value, bool):
            rendered = str(value).lower()
        else:
            rendered = json.dumps(str(value), ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    values: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        raw = raw.strip()
        try:
            values[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            values[key.strip()] = raw
    return values, text[end + 5:]


def canonical_pages(root: Path) -> Iterator[Path]:
    for directory in ["03-entities", "04-work", "05-knowledge", "06-execution"]:
        base = Path(root) / directory
        if base.exists():
            yield from (path for path in base.rglob("*.md") if path.name != ".gitkeep")
