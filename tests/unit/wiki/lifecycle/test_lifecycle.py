from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_steward_wiki.wiki.lifecycle import (
    AntiSpamCapError,
    TrashRetentionExpiredError,
    WikiLifecycleManager,
    WikiNotFoundError,
    levenshtein,
)


def test_levenshtein_basics() -> None:
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "abc") == 0


def test_create_and_lookup(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root)
    n = mgr.create_wiki(42, "здоровье", template_id="health")
    assert n.primary == "Zdorove-WIKI"
    assert (wiki_root / "42" / "Zdorove-WIKI" / "CLAUDE.md").exists()
    assert mgr.lookup(42, "zdorove") is not None
    assert mgr.lookup(42, "Zdorove-WIKI") is not None


def test_create_idempotent(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root)
    a = mgr.create_wiki(42, "Health", template_id="health")
    b = mgr.create_wiki(42, "Health", template_id="health")
    assert a.primary == b.primary
    assert len(mgr.list_active(42)) == 1


def test_cap_enforced(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root, max_per_user=2)
    mgr.create_wiki(7, "Alpha", template_id="_default")
    mgr.create_wiki(7, "Bravo", template_id="_default")
    with pytest.raises(AntiSpamCapError):
        mgr.create_wiki(7, "Charlie", template_id="_default")


def test_near_duplicate_returns_existing(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root)
    mgr.create_wiki(1, "Health", template_id="health")
    near = mgr.create_wiki(1, "Helth", template_id="health")  # distance 1
    assert near.primary == "Health-WIKI"
    # No second directory created
    assert len(mgr.list_active(1)) == 1


def test_soft_delete_moves_to_trash(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root)
    mgr.create_wiki(9, "Health", template_id="health")
    trashed = mgr.soft_delete(9, "Health-WIKI")
    assert not (wiki_root / "9" / "Health-WIKI").exists()
    assert trashed.trashed_path.exists()
    assert trashed.trashed_path.parent.name == "_trash"
    assert trashed.primary == "Health-WIKI"
    assert "_Health-WIKI" in trashed.trashed_path.name
    assert mgr.list_active(9) == []


def test_trash_excluded_from_cap(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root, max_per_user=1)
    mgr.create_wiki(3, "Health", template_id="health")
    mgr.soft_delete(3, "Health-WIKI")
    # cap should allow new creation because trash doesn't count
    mgr.create_wiki(3, "Budget", template_id="_default")
    assert {w.primary for w in mgr.list_active(3)} == {"Budget-WIKI"}


def test_restore_within_window(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root, retention_days=30)
    mgr.create_wiki(5, "Health", template_id="health")
    trashed = mgr.soft_delete(5, "Health-WIKI")
    restored = mgr.restore(5, trashed)
    assert restored.primary == "Health-WIKI"
    assert (wiki_root / "5" / "Health-WIKI").exists()


def test_restore_after_window_rejected(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root, retention_days=30)
    mgr.create_wiki(5, "Health", template_id="health")
    trashed = mgr.soft_delete(5, "Health-WIKI")
    future = datetime.now(tz=UTC) + timedelta(days=31)
    with pytest.raises(TrashRetentionExpiredError):
        mgr.restore(5, trashed, now_utc=future)


def test_soft_delete_missing_raises(wiki_root: Path) -> None:
    mgr = WikiLifecycleManager(wiki_root)
    with pytest.raises(WikiNotFoundError):
        mgr.soft_delete(11, "Ghost-WIKI")


# --- aisw-db6: create_wiki must materialize the template into the managed zone ---


def _seed_templates(tmp_path: Path) -> Path:
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "medical.md").write_text(
        "# Medical WIKI\n\n## Data layout\n1. `metrics/` — CSV: date,time,systolic,diastolic,pulse,context,flag\n",
        encoding="utf-8",
    )
    return tdir


def test_create_wiki_renders_managed_zone(wiki_root: Path, tmp_path: Path) -> None:
    from ai_steward_wiki.wiki.migration import MANAGED_START, USER_START, parse_frontmatter

    tdir = _seed_templates(tmp_path)
    mgr = WikiLifecycleManager(wiki_root, templates_dir=tdir)
    n = mgr.create_wiki(42, "медицина", template_id="medical")
    claude = wiki_root / "42" / n.primary / "CLAUDE.md"
    text = claude.read_text(encoding="utf-8")
    # managed zone carries the template's Data layout — the model now sees it
    assert MANAGED_START in text
    assert "## Data layout" in text
    assert "systolic,diastolic,pulse" in text
    assert USER_START in text  # editable user zone present
    fm, _ = parse_frontmatter(text)
    assert fm.template_sha256 != ""  # real sha, not the empty-schema bug


def test_create_wiki_without_templates_dir_is_frontmatter_only(wiki_root: Path) -> None:
    """Back-compat: no templates_dir (legacy callers/tests) → frontmatter-only, no crash."""
    from ai_steward_wiki.wiki.migration import parse_frontmatter

    mgr = WikiLifecycleManager(wiki_root)  # no templates_dir
    n = mgr.create_wiki(7, "Alpha", template_id="_default")
    text = (wiki_root / "7" / n.primary / "CLAUDE.md").read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(text)
    assert fm.template_id == "_default"
    assert fm.schema_version == 2


def test_create_wiki_unknown_template_falls_back_to_default(
    wiki_root: Path, tmp_path: Path
) -> None:
    """Unknown template_id with a templates_dir present → _default schema, never crash."""
    from ai_steward_wiki.wiki.migration import MANAGED_START

    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "_default.md").write_text("# Default\n\n## Format\nlists\n", encoding="utf-8")
    mgr = WikiLifecycleManager(wiki_root, templates_dir=tdir)
    n = mgr.create_wiki(9, "Mystery", template_id="nonexistent")
    text = (wiki_root / "9" / n.primary / "CLAUDE.md").read_text(encoding="utf-8")
    assert MANAGED_START in text
    assert "## Format" in text  # fell back to _default body
