from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_steward_wiki.llm.codex import CodexRequest
from ai_steward_wiki.llm.failover import (
    AttemptEvidence,
    EvidenceKind,
    FailoverPolicy,
    ProviderLimitError,
)
from ai_steward_wiki.wiki.migration import (
    MANAGED_START,
    USER_START,
    parse_frontmatter,
)
from ai_steward_wiki.wiki.schema_gen import (
    GENERATED_TEMPLATE_ID,
    ClaudeCliSchemaGenerator,
    FailoverSchemaGenerator,
    FakeSchemaGenerator,
    SchemaGenError,
    apply_generated_schema,
    validate_schema,
)

_GOOD = (
    "## Data layout\n1. `episodes/` — CSV\n"
    "## File resolution\nappend to existing\n"
    "## Inbox hint\nintents: append_data\n"
    "## Персонажи\nкарточка на персонажа\n"
)


class _StubSpawner:
    def __init__(self, *, rc: int, stdout: bytes, stderr: bytes = b"") -> None:
        self.result = (rc, stdout, stderr)

    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        stdin: bytes,
        timeout_s: float,
        cwd: str | None = None,
    ) -> tuple[int, bytes, bytes]:
        return self.result


class _StubCodex:
    complex_model = "gpt-5.5"
    complex_reasoning = "medium"
    neutral_cwd = Path("/tmp/codex-runtime")

    def __init__(self, *, result: str = _GOOD, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[CodexRequest] = []

    async def run_text(self, request: CodexRequest) -> str:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def _limit() -> ProviderLimitError:
    return ProviderLimitError(
        provider="claude",
        reset_at=None,
        evidence=AttemptEvidence(EvidenceKind.READ_ONLY),
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


def test_cli_generator_argv_is_toolless_json_envelope_turn(tmp_path: Path) -> None:
    prompt = tmp_path / "schema-gen.md"
    prompt.write_text("semver: 1.0.0\n# schema gen system prompt\n", encoding="utf-8")
    gen = ClaudeCliSchemaGenerator(claude_config_dir=tmp_path / "cfg", prompt_path=prompt)
    argv = gen._argv()
    assert "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--tools") + 1] == ""  # no tools — pure generation
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-5"


async def test_cli_generator_unwraps_success_envelope(tmp_path: Path) -> None:
    prompt = tmp_path / "schema-gen.md"
    prompt.write_text("system prompt", encoding="utf-8")
    envelope = json.dumps(
        {"type": "result", "subtype": "success", "is_error": False, "result": _GOOD}
    ).encode()
    generator = ClaudeCliSchemaGenerator(
        claude_config_dir=tmp_path / "cfg",
        prompt_path=prompt,
        spawner=_StubSpawner(rc=0, stdout=envelope),  # type: ignore[arg-type]
    )

    assert (
        await generator.generate(
            wiki_name="Anime-WIKI",
            first_content="source",
            correlation_id="corr-1",
        )
        == _GOOD.strip()
    )


async def test_cli_generator_raises_typed_limit(tmp_path: Path) -> None:
    prompt = tmp_path / "schema-gen.md"
    prompt.write_text("system prompt", encoding="utf-8")
    envelope = json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "api_error_status": 429,
            "result": "subscription limit reached",
        }
    ).encode()
    generator = ClaudeCliSchemaGenerator(
        claude_config_dir=tmp_path / "cfg",
        prompt_path=prompt,
        spawner=_StubSpawner(rc=1, stdout=envelope),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderLimitError):
        await generator.generate(
            wiki_name="Anime-WIKI",
            first_content="source",
            correlation_id="corr-2",
        )


async def test_schema_limit_uses_complex_codex_before_apply(tmp_path: Path) -> None:
    primary = FakeSchemaGenerator(raises=_limit())
    codex = _StubCodex()
    prompt = tmp_path / "schema-gen.md"
    prompt.write_text("SCHEMA SYSTEM", encoding="utf-8")
    generator = FailoverSchemaGenerator(
        primary=primary,
        codex=codex,  # type: ignore[arg-type]
        policy=FailoverPolicy(cooldown_s=900.0),
        prompt_path=prompt,
        timeout_s=60.0,
    )

    result = await generator.generate(
        wiki_name="Anime-WIKI",
        first_content="source",
        correlation_id="corr-3",
    )

    assert result == _GOOD
    request = codex.calls[0]
    assert request.model == "gpt-5.5"
    assert request.reasoning == "medium"
    assert "SCHEMA SYSTEM" in request.prompt
    assert "Anime-WIKI" in request.prompt


async def test_schema_generic_failure_does_not_use_codex(tmp_path: Path) -> None:
    primary = FakeSchemaGenerator(raises=SchemaGenError("generic"))
    codex = _StubCodex()
    prompt = tmp_path / "schema-gen.md"
    prompt.write_text("SCHEMA SYSTEM", encoding="utf-8")
    generator = FailoverSchemaGenerator(
        primary=primary,
        codex=codex,  # type: ignore[arg-type]
        policy=FailoverPolicy(cooldown_s=900.0),
        prompt_path=prompt,
        timeout_s=60.0,
    )

    with pytest.raises(SchemaGenError, match="generic"):
        await generator.generate(
            wiki_name="Anime-WIKI",
            first_content="source",
            correlation_id="corr-4",
        )

    assert codex.calls == []


def test_required_sections_are_the_contract() -> None:
    from ai_steward_wiki.wiki.schema_gen import REQUIRED_SECTIONS

    assert "## Data layout" in REQUIRED_SECTIONS
    assert "## File resolution" in REQUIRED_SECTIONS
    assert "## Inbox hint" in REQUIRED_SECTIONS
