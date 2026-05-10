"""Tests for ensure_inbox_wiki (D-004, D-016)."""

from __future__ import annotations

from pathlib import Path

from ai_steward_wiki.inbox.materialize import INBOX_WIKI_DIRNAME, ensure_inbox_wiki

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = REPO_ROOT / "templates" / "inbox-wiki" / "CLAUDE.md"


async def test_creates_dir_with_claude_md(tmp_path) -> None:
    out = await ensure_inbox_wiki(42, wiki_root=tmp_path, template_path=TEMPLATE_PATH)
    assert out == tmp_path / "42" / INBOX_WIKI_DIRNAME
    assert out.is_dir()
    md = out / "CLAUDE.md"
    assert md.is_file()
    content = md.read_text(encoding="utf-8")
    assert "Inbox-WIKI" in content
    assert "## Intent vocabulary" in content


async def test_idempotent_second_call(tmp_path) -> None:
    out1 = await ensure_inbox_wiki(7, wiki_root=tmp_path, template_path=TEMPLATE_PATH)
    md = out1 / "CLAUDE.md"
    md.write_text("MUTATED\n", encoding="utf-8")  # simulate user edit
    out2 = await ensure_inbox_wiki(7, wiki_root=tmp_path, template_path=TEMPLATE_PATH)
    assert out1 == out2
    # Idempotent: existing CLAUDE.md preserved, not overwritten.
    assert md.read_text(encoding="utf-8") == "MUTATED\n"


async def test_no_tmp_leftover_after_success(tmp_path) -> None:
    out = await ensure_inbox_wiki(99, wiki_root=tmp_path, template_path=TEMPLATE_PATH)
    leftovers = list(out.glob("*.tmp"))
    assert leftovers == []


async def test_separate_users_isolated(tmp_path) -> None:
    a = await ensure_inbox_wiki(1, wiki_root=tmp_path, template_path=TEMPLATE_PATH)
    b = await ensure_inbox_wiki(2, wiki_root=tmp_path, template_path=TEMPLATE_PATH)
    assert a != b
    assert a.parent.name == "1"
    assert b.parent.name == "2"
