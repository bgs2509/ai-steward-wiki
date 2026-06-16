from __future__ import annotations

from pathlib import Path

from ai_steward_wiki.wiki.migration import (
    MANAGED_START,
    USER_START,
    parse_frontmatter,
)
from ai_steward_wiki.wiki.schema_gen import (
    GENERATED_TEMPLATE_ID,
    ClaudeCliSchemaGenerator,
    FakeSchemaGenerator,
    apply_generated_schema,
    validate_schema,
)

_GOOD = (
    "## Data layout\n1. `episodes/` — CSV\n"
    "## File resolution\nappend to existing\n"
    "## Inbox hint\nintents: append_data\n"
    "## Персонажи\nкарточка на персонажа\n"
)


def _v2_default(path: Path) -> None:
    """Seed a freshly-created v2 CLAUDE.md (as create_wiki writes for unknown domains)."""
    path.write_text(
        "---\nschema_version: 2\ntemplate_id: _default\n"
        "last_migrated_at: 2026-01-01T00:00:00Z\ntemplate_sha256: defaultsha\n---\n"
        f"{MANAGED_START}\n# Default\n## Data layout\ngeneric\n<!-- managed:end -->\n\n"
        f"{USER_START}\n\n<!-- user:end -->\n",
        encoding="utf-8",
    )


# ---------- validate_schema ----------


def test_validate_schema_accepts_required_plus_topic() -> None:
    assert validate_schema(_GOOD) is True


def test_validate_schema_rejects_missing_required_section() -> None:
    no_inbox = _GOOD.replace("## Inbox hint\nintents: append_data\n", "")
    assert validate_schema(no_inbox) is False


def test_validate_schema_rejects_no_topic_section() -> None:
    only_required = "## Data layout\nx\n## File resolution\ny\n## Inbox hint\nz\n"
    assert validate_schema(only_required) is False


# ---------- apply_generated_schema ----------


async def test_apply_writes_generated_managed_zone(tmp_path: Path) -> None:
    claude = tmp_path / "CLAUDE.md"
    _v2_default(claude)
    gen = FakeSchemaGenerator(canned=_GOOD)
    applied = await apply_generated_schema(
        claude_md=claude,
        wiki_name="Anime-Maguro-WIKI",
        first_content="инфа про магуро аниме",
        correlation_id="c1",
        generator=gen,
    )
    assert applied is True
    text = claude.read_text(encoding="utf-8")
    assert "## Персонажи" in text  # topic-specific section landed
    assert "generic" not in text  # old _default managed replaced
    fm, _ = parse_frontmatter(text)
    assert fm.template_id == GENERATED_TEMPLATE_ID  # marks it as non-clobberable
    assert gen.calls[0]["wiki_name"] == "Anime-Maguro-WIKI"


async def test_apply_returns_false_on_invalid_schema_keeps_fallback(tmp_path: Path) -> None:
    claude = tmp_path / "CLAUDE.md"
    _v2_default(claude)
    before = claude.read_text(encoding="utf-8")
    gen = FakeSchemaGenerator(canned="## Data layout only, no topic, no inbox")
    applied = await apply_generated_schema(
        claude_md=claude,
        wiki_name="X-WIKI",
        first_content="...",
        correlation_id="c2",
        generator=gen,
    )
    assert applied is False
    assert claude.read_text(encoding="utf-8") == before  # _default fallback untouched


async def test_apply_returns_false_on_generator_failure(tmp_path: Path) -> None:
    claude = tmp_path / "CLAUDE.md"
    _v2_default(claude)
    before = claude.read_text(encoding="utf-8")
    gen = FakeSchemaGenerator(raises=RuntimeError("boom"))
    applied = await apply_generated_schema(
        claude_md=claude,
        wiki_name="X-WIKI",
        first_content="...",
        correlation_id="c3",
        generator=gen,
    )
    assert applied is False
    assert claude.read_text(encoding="utf-8") == before


# ---------- ClaudeCliSchemaGenerator argv ----------


def test_cli_generator_argv_is_toolless_text_turn(tmp_path: Path) -> None:
    prompt = tmp_path / "schema-gen.md"
    prompt.write_text("semver: 1.0.0\n# schema gen system prompt\n", encoding="utf-8")
    gen = ClaudeCliSchemaGenerator(claude_config_dir=tmp_path / "cfg", prompt_path=prompt)
    argv = gen._argv()
    assert "-p" in argv
    assert "--output-format" not in argv  # text mode (print), not json
    assert argv[argv.index("--tools") + 1] == ""  # no tools — pure generation
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-5"


def test_required_sections_are_the_contract() -> None:
    from ai_steward_wiki.wiki.schema_gen import REQUIRED_SECTIONS

    assert "## Data layout" in REQUIRED_SECTIONS
    assert "## File resolution" in REQUIRED_SECTIONS
    assert "## Inbox hint" in REQUIRED_SECTIONS
