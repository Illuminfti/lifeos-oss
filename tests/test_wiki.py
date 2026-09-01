from __future__ import annotations

from pathlib import Path

import pytest

from lifeos.errors import StaleProposal, UnsafePath
from lifeos.wiki import canonical_path, file_revision, render_entity_page, write_canonical


def test_exact_brain_tree_exists(brain):
    required = (
        "00-dashboards",
        "01-inbox",
        "02-staging",
        "03-entities",
        "04-work",
        "05-knowledge",
        "06-execution",
        "07-raw",
        "99-archive",
        ".lifeos/config.json",
    )
    for relative in required:
        assert (brain.root / relative).exists(), relative


def test_canonical_paths_cannot_escape(brain):
    with pytest.raises(UnsafePath):
        canonical_path(brain, "../../etc/passwd")
    with pytest.raises(UnsafePath):
        canonical_path(brain, "02-staging/not-canon.md")


def test_revision_checked_atomic_canonical_write(brain):
    relative = "03-entities/people/ada.md"
    content = render_entity_page(
        entity_id="ent_ada",
        title="Ada",
        entity_type="person",
        sources=["event:1"],
        compiled_truth="Known from reviewed evidence.",
        timeline=["- 2026-09-01: Met. Evidence: `event:1`."],
    )
    before, after = write_canonical(brain, relative, content, expected_revision="missing")
    assert before == "missing"
    assert after == file_revision(brain.root / relative)
    with pytest.raises(StaleProposal):
        write_canonical(brain, relative, content, expected_revision="missing")
