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
