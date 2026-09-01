"""Canonical Markdown tree. Operational state lives under .lifeos/."""
from __future__ import annotations

from pathlib import Path

TREE = [
    "CANON.md",
    "SCHEMA.md",
    "README.md",
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
]

CANON = """# Canon

This directory is the sole canonical memory home for this LifeOS brain.

GBrain and pgGraph are derived. Staging is not canon. Raw evidence is not canon.
Agents write proposals under 02-staging/. Only an owner promotion writes 03-entities/ and above.
"""

SCHEMA = """# Schema

Canonical pages use YAML frontmatter: id, title, type, status, aliases, sources,
confidence, reviewed, next_review, sensitivity.

Operational state is `.lifeos/` and is not indexed as canon.
"""

GITIGNORE_STATE = """*
!.gitignore
"""


def init_brain(path: Path) -> Path:
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for rel in TREE:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".gitignore"):
            dest.write_text(GITIGNORE_STATE)
        elif rel.endswith(".gitkeep"):
            dest.write_text("")
        elif rel == "CANON.md":
            dest.write_text(CANON)
        elif rel == "SCHEMA.md":
            dest.write_text(SCHEMA)
        elif rel == "README.md":
            dest.write_text("# LifeOS brain\n\nCanonical Markdown. See CANON.md.\n")
        elif not dest.exists():
            dest.write_text("")
    (root / ".lifeos").mkdir(parents=True, exist_ok=True)
    return root
