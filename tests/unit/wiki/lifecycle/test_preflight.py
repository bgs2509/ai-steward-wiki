from __future__ import annotations

from pathlib import Path

from ai_steward_wiki.wiki.preflight import preflight


def _make_wiki(wiki_root: Path, schema_version: int = 2, template_id: str = "health") -> Path:
    w = wiki_root / "1" / "Health-WIKI"
    w.mkdir(parents=True)
    (w / "CLAUDE.md").write_text(
        f"---\nschema_version: {schema_version}\ntemplate_id: {template_id}\n"
        "last_migrated_at: 2026-05-10T00:00:00Z\ntemplate_sha256: x\n---\nbody\n",
        encoding="utf-8",
    )
    return w


def test_all_pass(wiki_root: Path, template_dir: Path) -> None:
    w = _make_wiki(wiki_root)
    rep = preflight(wiki_path=w, template_dir=template_dir)
    assert rep.ok is True
    assert [c.name for c in rep.checks] == [
        "locks",
        "frontmatter",
        "template",
        "staging",
        "permissions",
    ]


def test_locks_fail_when_lock_file_present(wiki_root: Path, template_dir: Path) -> None:
    w = _make_wiki(wiki_root)
    (w / ".wiki.lock").write_text("4242", encoding="utf-8")
    rep = preflight(wiki_path=w, template_dir=template_dir)
    assert rep.ok is False
    locks = next(c for c in rep.checks if c.name == "locks")
    assert locks.ok is False


def test_frontmatter_wrong_version(wiki_root: Path, template_dir: Path) -> None:
    w = _make_wiki(wiki_root, schema_version=1)
    rep = preflight(wiki_path=w, template_dir=template_dir)
    fm = next(c for c in rep.checks if c.name == "frontmatter")
    assert fm.ok is False


def test_template_missing(wiki_root: Path, template_dir: Path) -> None:
    w = _make_wiki(wiki_root, template_id="ghost")
    rep = preflight(wiki_path=w, template_dir=template_dir)
    tpl = next(c for c in rep.checks if c.name == "template")
    assert tpl.ok is False


def test_staging_too_big(wiki_root: Path, template_dir: Path, tmp_path: Path) -> None:
    w = _make_wiki(wiki_root)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "big.bin").write_bytes(b"x" * 1024)
    rep = preflight(
        wiki_path=w,
        template_dir=template_dir,
        staging_dir=staging,
        max_staging_bytes=10,
    )
    stg = next(c for c in rep.checks if c.name == "staging")
    assert stg.ok is False


def test_permissions_missing_path(wiki_root: Path, template_dir: Path) -> None:
    ghost = wiki_root / "1" / "Ghost-WIKI"
    rep = preflight(wiki_path=ghost, template_dir=template_dir)
    perm = next(c for c in rep.checks if c.name == "permissions")
    assert perm.ok is False
