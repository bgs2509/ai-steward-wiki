from __future__ import annotations

from pathlib import Path

import pytest

from ai_steward_wiki.wiki.migration import (
    TemplateNotFoundError,
    load_template,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "templates"


def test_required_templates_exist() -> None:
    from scripts.lint_templates import REQUIRED_TEMPLATES

    for template_id in REQUIRED_TEMPLATES:
        assert (TEMPLATES_DIR / f"{template_id}.md").is_file(), template_id


def test_lint_templates_clean_on_repo_templates() -> None:
    from scripts.lint_templates import lint_dir

    results = lint_dir(TEMPLATES_DIR)
    assert results == {}, results


def test_lint_detects_missing_inbox_hint(tmp_path: Path) -> None:
    from scripts.lint_templates import lint_template

    p = tmp_path / "broken.md"
    p.write_text("# Broken\n\nno hint section here.\n", encoding="utf-8")
    errs = lint_template(p)
    assert any("Inbox hint" in e for e in errs)


def test_lint_detects_missing_h1(tmp_path: Path) -> None:
    from scripts.lint_templates import lint_template

    p = tmp_path / "no_h1.md"
    p.write_text(
        "## Inbox hint\nintents: query\nkeywords: x\npriority: 50\n",
        encoding="utf-8",
    )
    errs = lint_template(p)
    assert any("H1" in e for e in errs)


def test_lint_detects_markdown_table(tmp_path: Path) -> None:
    from scripts.lint_templates import lint_template

    p = tmp_path / "table.md"
    p.write_text(
        "# T\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## Inbox hint\n"
        "intents: query\nkeywords: x\npriority: 50\n",
        encoding="utf-8",
    )
    errs = lint_template(p)
    assert any("table" in e.lower() for e in errs)


def test_lint_detects_priority_out_of_range(tmp_path: Path) -> None:
    from scripts.lint_templates import lint_template

    p = tmp_path / "bad_prio.md"
    p.write_text(
        "# T\n\n## Inbox hint\nintents: query\nkeywords: x\npriority: 999\n",
        encoding="utf-8",
    )
    errs = lint_template(p)
    assert any("priority" in e for e in errs)


def test_lint_main_fails_on_missing_with_flag(tmp_path: Path) -> None:
    from scripts.lint_templates import main

    rc = main(["--templates-dir", str(tmp_path), "--fail-on-error"])
    assert rc == 1


def test_load_template_returns_sha256() -> None:
    managed, digest = load_template("medical", TEMPLATES_DIR)
    assert "Medical WIKI" in managed
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_load_template_unknown_raises() -> None:
    with pytest.raises(TemplateNotFoundError):
        load_template("does_not_exist", TEMPLATES_DIR)
