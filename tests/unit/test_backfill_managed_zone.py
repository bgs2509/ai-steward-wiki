from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from ai_steward_wiki.wiki.migration import MANAGED_START, parse_frontmatter

# scripts/ is not a package — load the module by path.
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backfill_managed_zone.py"
_spec = importlib.util.spec_from_file_location("backfill_managed_zone", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
backfill_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill_mod)


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "medical.md").write_text(
        "# Medical\n## Data layout\n1. metrics/ -> CSV date,time,systolic\n", encoding="utf-8"
    )
    wikis = tmp_path / "wikis"
    claude = wikis / "42" / "Medical-WIKI" / "CLAUDE.md"
    claude.parent.mkdir(parents=True)
    claude.write_text(
        "---\nschema_version: 2\ntemplate_id: medical\n"
        "last_migrated_at: 2026-01-01T00:00:00Z\ntemplate_sha256: \n---\n",
        encoding="utf-8",
    )
    # a trash dir that must be skipped
    (wikis / "42" / "_trash").mkdir()
    return wikis, templates, claude


def test_backfill_fills_empty_managed_zone(tree: tuple[Path, Path, Path]) -> None:
    wikis, templates, claude = tree
    counts = backfill_mod.backfill(wikis, templates)
    assert counts["fixed"] == 1
    text = claude.read_text(encoding="utf-8")
    assert MANAGED_START in text
    assert "## Data layout" in text
    fm, _ = parse_frontmatter(text)
    assert fm.template_sha256 != ""


def test_backfill_is_idempotent(tree: tuple[Path, Path, Path]) -> None:
    wikis, templates, _ = tree
    backfill_mod.backfill(wikis, templates)
    counts2 = backfill_mod.backfill(wikis, templates)
    assert counts2["fixed"] == 0
    assert counts2["noop"] == 1


def test_backfill_dry_run_does_not_write(tree: tuple[Path, Path, Path]) -> None:
    wikis, templates, claude = tree
    before = claude.read_text(encoding="utf-8")
    counts = backfill_mod.backfill(wikis, templates, dry_run=True)
    assert counts["fixed"] == 1  # would fix
    assert claude.read_text(encoding="utf-8") == before  # but did not write


def test_backfill_skips_unknown_template(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    wikis = tmp_path / "wikis"
    claude = wikis / "7" / "Mystery-WIKI" / "CLAUDE.md"
    claude.parent.mkdir(parents=True)
    claude.write_text(
        "---\nschema_version: 2\ntemplate_id: nope\n"
        "last_migrated_at: 2026-01-01T00:00:00Z\ntemplate_sha256: \n---\n",
        encoding="utf-8",
    )
    counts = backfill_mod.backfill(wikis, templates)
    assert counts["skipped"] == 1
    assert counts["fixed"] == 0
