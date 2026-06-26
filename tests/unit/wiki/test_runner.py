from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_steward_wiki.wiki.runner import (
    WikiRunnerError,
    WikiRunnerTimeoutError,
    _RunConfig,
    assemble_prompt,
    run_wiki_session,
)
from tests.unit.wiki.conftest import FakeAcquirer, FakeSpawner


def _cfg(claude_config_dir: Path, **overrides: object) -> _RunConfig:
    base: dict[str, object] = {
        "claude_config_dir": claude_config_dir,
        "timeout_s": 2.0,
        "term_grace_s": 0.1,
    }
    base.update(overrides)
    return _RunConfig(**base)  # type: ignore[arg-type]


def _make_lines() -> list[bytes]:
    return [
        json.dumps({"type": "assistant", "text": "hi"}).encode() + b"\n",
        json.dumps({"type": "result", "stop_reason": "end_turn"}).encode() + b"\n",
    ]


def test_assemble_prompt_concatenates_and_atomic(prompts_dir: Path, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    target = assemble_prompt(
        base_path=prompts_dir / "wiki.md",
        overlay_path=prompts_dir / "inbox.md",
        runtime_dir=runtime,
        run_id="r1",
    )
    text = target.read_text(encoding="utf-8")
    assert "# base" in text
    assert "# inbox overlay" in text
    # tmp must have been replaced — no .tmp sibling.
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_assemble_prompt_requires_semver(tmp_path: Path) -> None:
    base = tmp_path / "base.md"
    base.write_text("# no semver line\n", encoding="utf-8")
    overlay = tmp_path / "ov.md"
    overlay.write_text("semver: 1.0.0\n# ov\n", encoding="utf-8")
    with pytest.raises(WikiRunnerError, match="semver"):
        assemble_prompt(
            base_path=base,
            overlay_path=overlay,
            runtime_dir=tmp_path / "rt",
            run_id="r",
        )


async def test_run_wiki_session_happy_path(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    wiki = tmp_path / "Medical-WIKI"
    runtime = tmp_path / "runtime"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()

    result = await run_wiki_session(
        wiki_id="Medical-WIKI",
        wiki_path=wiki,
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-001",
        correlation_id="corr-1",
        runtime_dir=runtime,
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir),
    )

    assert result.exit_code == 0
    assert [e.type for e in result.events] == ["assistant_chunk", "final"]
    assert result.transcript_path.exists()
    assert not result.transcript_path.with_suffix(result.transcript_path.suffix + ".tmp").exists()
    argv = spawner.calls[0]["argv"]
    assert isinstance(argv, list)
    assert "--add-dir" in argv
    assert str(wiki) in argv
    assert "stream-json" in argv
    # aisw-kpb: claude CLI rejects --print + --output-format stream-json without --verbose.
    assert "--verbose" in argv
    assert ("stream-json" not in argv) or ("--verbose" in argv)
    # FR-2: inline replace form. `--system-prompt-file` does NOT replace the default
    # Claude Code system prompt under subscription auth (verified 2026-05-12, bd aisw-adj);
    # content must be inlined via `--system-prompt`. No @-prefix on prompt path, no append.
    assert "--system-prompt" in argv
    assert "--system-prompt-file" not in argv
    assert "--append-system-prompt" not in argv
    assert not any(str(a).startswith("@") for a in argv)
    # aisw-0mg: -p + isolation flags required so default Claude Code persona
    # does not leak into Stage-1 wiki edits under subscription OAuth.
    assert "-p" in argv
    ss = argv.index("--setting-sources")
    assert argv[ss + 1] == ""
    assert "--disable-slash-commands" in argv
    # aisw-22o (reverses old FR-3): cwd IS the WIKI dir — the base/domain prompts use
    # WIKI-relative paths (raw/, metrics/, log.md), so the model must run inside it.
    assert spawner.calls[0]["cwd"] == str(wiki)
    # env: CLAUDE_CONFIG_DIR + minimal PATH.
    env = spawner.calls[0]["env"]
    assert isinstance(env, dict)
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg_dir)
    assert env["PATH"] == "/usr/bin:/bin"
    assert fake_acquirer.calls == [("Medical-WIKI", wiki)]
    # aisw-w83: when user_input is empty, stdin_data is None (DEVNULL on the
    # real spawner path); the runner does not synthesize input.
    assert spawner.calls[0]["stdin_data"] is None


async def test_run_wiki_session_grants_media_dirs_via_add_dir(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    wiki = tmp_path / "Medical-WIKI"
    media_dir = tmp_path / "_staging"
    media_dir.mkdir()
    img = media_dir / "run-img_abcd1234.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()

    await run_wiki_session(
        wiki_id="Medical-WIKI",
        wiki_path=wiki,
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-002",
        correlation_id="corr-2",
        runtime_dir=tmp_path / "runtime",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir),
        media_paths=[img],
    )
    argv = spawner.calls[0]["argv"]
    # --add-dir is variadic: wiki path first, then each media dir.
    add_idx = argv.index("--add-dir")
    assert argv[add_idx + 1] == str(wiki)
    assert str(media_dir) in argv
    # media dir appears right after the wiki path, before the system-prompt flag.
    assert argv[add_idx + 2] == str(media_dir)


async def test_run_wiki_session_no_media_argv_unchanged(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    wiki = tmp_path / "Medical-WIKI"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()
    await run_wiki_session(
        wiki_id="Medical-WIKI",
        wiki_path=wiki,
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-003",
        correlation_id="corr-3",
        runtime_dir=tmp_path / "runtime",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir),
    )
    argv = spawner.calls[0]["argv"]
    add_idx = argv.index("--add-dir")
    # Only the wiki path follows --add-dir; next token is the system-prompt flag.
    assert argv[add_idx + 1] == str(wiki)
    assert argv[add_idx + 2].startswith("--")


async def test_run_wiki_session_pipes_user_input_to_stdin(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """aisw-w83: user_input is delivered to claude via stdin, not the system prompt."""
    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    wiki = tmp_path / "Medical-WIKI"
    runtime = tmp_path / "runtime"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()

    await run_wiki_session(
        wiki_id="Medical-WIKI",
        wiki_path=wiki,
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-stdin",
        correlation_id="corr-stdin",
        runtime_dir=runtime,
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir),
        user_input="что ты умеешь",
    )

    assert spawner.calls[0]["stdin_data"] == "что ты умеешь".encode()
    # Overlay (assembled system prompt) must NOT contain the user turn.
    system_prompt_file = runtime / "run-stdin.system.md"
    assert system_prompt_file.exists()
    assert "что ты умеешь" not in system_prompt_file.read_text(encoding="utf-8")


async def test_run_wiki_session_timeout_invokes_kill(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    spawner = FakeSpawner(lines=[], exit_code=0, hang=True)
    wiki = tmp_path / "Slow-WIKI"
    runtime = tmp_path / "runtime"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()

    with pytest.raises(WikiRunnerTimeoutError):
        await run_wiki_session(
            wiki_id="Slow-WIKI",
            wiki_path=wiki,
            base_prompt_path=prompts_dir / "wiki.md",
            overlay_prompt_path=prompts_dir / "domain-default.md",
            run_id="run-timeout",
            correlation_id="corr-2",
            runtime_dir=runtime,
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_cfg(cfg_dir, timeout_s=0.1, term_grace_s=0.05),
        )

    transcript = wiki / "runs" / "run-timeout" / "transcript.jsonl"
    assert transcript.exists()


async def test_run_wiki_session_timeout_s_overrides_config(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    spawner = FakeSpawner(lines=[], exit_code=0, hang=True)
    wiki = tmp_path / "Slow-WIKI"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()

    # config.timeout_s is generous (5s) — the per-call override (0.05s) wins.
    with pytest.raises(WikiRunnerTimeoutError, match="0.05"):
        await run_wiki_session(
            wiki_id="Slow-WIKI",
            wiki_path=wiki,
            base_prompt_path=prompts_dir / "wiki.md",
            overlay_prompt_path=prompts_dir / "domain-default.md",
            run_id="run-to-override",
            correlation_id="corr-to",
            runtime_dir=tmp_path / "runtime",
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_cfg(cfg_dir, timeout_s=5.0, term_grace_s=0.05),
            timeout_s=0.05,
        )


async def test_run_wiki_session_nonzero_exit_raises_with_stderr(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """FR-4: rc != 0 → drain stderr, log wiki.run.error, raise WikiRunnerError."""
    spawner = FakeSpawner(
        lines=[],
        stderr_bytes=b"error: unrecognized arguments: --append-system-prompt",
        exit_code=1,
    )
    wiki = tmp_path / "Broken-WIKI"
    runtime = tmp_path / "runtime"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()

    with pytest.raises(WikiRunnerError, match=r"rc=1.*unrecognized arguments"):
        await run_wiki_session(
            wiki_id="Broken-WIKI",
            wiki_path=wiki,
            base_prompt_path=prompts_dir / "wiki.md",
            overlay_prompt_path=prompts_dir / "domain-default.md",
            run_id="run-fail",
            correlation_id="corr-3",
            runtime_dir=runtime,
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_cfg(cfg_dir),
        )

    # Transcript still persisted before raise.
    transcript = wiki / "runs" / "run-fail" / "transcript.jsonl"
    assert transcript.exists()


def test_assemble_prompt_folds_per_wiki_claude_md(prompts_dir: Path, tmp_path: Path) -> None:
    """FR-3 derivative: per-WIKI CLAUDE.md is appended to assembled prompt."""
    wiki = tmp_path / "Medical-WIKI"
    wiki.mkdir()
    (wiki / "CLAUDE.md").write_text("# Per-WIKI overlay text\n", encoding="utf-8")

    target = assemble_prompt(
        base_path=prompts_dir / "wiki.md",
        overlay_path=prompts_dir / "inbox.md",
        runtime_dir=tmp_path / "runtime",
        run_id="r2",
        wiki_path=wiki,
    )
    text = target.read_text(encoding="utf-8")
    assert "# base" in text
    assert "# inbox overlay" in text
    assert "Per-WIKI overlay text" in text
    # Per-WIKI content appears AFTER the overlay.
    assert text.index("inbox overlay") < text.index("Per-WIKI overlay text")


def test_assemble_prompt_without_per_wiki_file(prompts_dir: Path, tmp_path: Path) -> None:
    """wiki_path given but no CLAUDE.md → no error, no extra content."""
    wiki = tmp_path / "Empty-WIKI"
    wiki.mkdir()
    target = assemble_prompt(
        base_path=prompts_dir / "wiki.md",
        overlay_path=prompts_dir / "inbox.md",
        runtime_dir=tmp_path / "runtime",
        run_id="r3",
        wiki_path=wiki,
    )
    text = target.read_text(encoding="utf-8")
    assert "Per-WIKI" not in text


async def test_run_wiki_session_extra_add_dirs_after_primary(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """aisw-oqq: extra_add_dirs appear after the primary --add-dir, before media dirs."""
    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    wiki = tmp_path / "Medical-WIKI"
    finance = tmp_path / "Finance-WIKI"
    cooking = tmp_path / "Cooking-WIKI"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()
    await run_wiki_session(
        wiki_id="Medical-WIKI",
        wiki_path=wiki,
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-extra",
        correlation_id="corr-extra",
        runtime_dir=tmp_path / "runtime",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir),
        extra_add_dirs=[finance, cooking],
    )
    argv = spawner.calls[0]["argv"]
    add_idx = argv.index("--add-dir")
    assert argv[add_idx + 1] == str(wiki)
    assert str(finance) in argv
    assert str(cooking) in argv
    assert argv.index(str(finance)) > add_idx + 1


# --- aisw-t6w: writing-run permissions + permission_denials surfacing ---


def _make_lines_with_denials() -> list[bytes]:
    """A final result event carrying non-empty permission_denials (Write+Edit)."""
    denials = [
        {"tool_name": "Edit", "tool_use_id": "toolu_a", "tool_input": {"file_path": "x.csv"}},
        {"tool_name": "Write", "tool_use_id": "toolu_b", "tool_input": {"file_path": "log.md"}},
    ]
    return [
        json.dumps({"type": "assistant", "text": "I need permission"}).encode() + b"\n",
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "permission_denials": denials,
            }
        ).encode()
        + b"\n",
    ]


async def test_writing_run_emits_allowedtools_flag(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """aisw-t6w Fix 1: allowed_tools on the config surfaces as --allowedTools <tools>."""
    from ai_steward_wiki.wiki.runner import WRITE_TOOLS

    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()
    await run_wiki_session(
        wiki_id="Medical-WIKI",
        wiki_path=tmp_path / "Medical-WIKI",
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-allow",
        correlation_id="corr-allow",
        runtime_dir=tmp_path / "runtime",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir, allowed_tools=list(WRITE_TOOLS)),
    )
    argv = spawner.calls[0]["argv"]
    assert "--allowedTools" in argv
    flag_idx = argv.index("--allowedTools")
    following = argv[flag_idx + 1 : flag_idx + 1 + len(WRITE_TOOLS)]
    assert following == list(WRITE_TOOLS)
    # dontAsk must remain — allowedTools is additive, not a mode switch.
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "Write" in WRITE_TOOLS
    assert "Edit" in WRITE_TOOLS


async def test_readonly_run_omits_allowedtools_flag(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """aisw-t6w: read-only runs (router/classifier) keep allowed_tools=None → no flag."""
    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()
    await run_wiki_session(
        wiki_id="Inbox-WIKI",
        wiki_path=tmp_path / "Inbox-WIKI",
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-ro",
        correlation_id="corr-ro",
        runtime_dir=tmp_path / "runtime",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir),  # allowed_tools defaults to None
    )
    argv = spawner.calls[0]["argv"]
    assert "--allowedTools" not in argv


async def test_web_task_run_config_allows_websearch_neutral_cwd(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """aisw-dqz (Path B): a web_search run allows WebSearch ONLY, runs read-only (no
    WRITE_TOOLS), keeps WebFetch denied, and is launched with NO --add-dir on the WIKI
    tree + a neutral cwd (prompt-injection mitigation M-1)."""
    from ai_steward_wiki.wiki.runner import WEB_SEARCH_TOOLS

    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()
    wiki = tmp_path / "Cooking-WIKI"
    await run_wiki_session(
        wiki_id="42",
        wiki_path=wiki,
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-web",
        correlation_id="corr-web",
        runtime_dir=tmp_path / "runtime",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir, allowed_tools=list(WEB_SEARCH_TOOLS), web_search=True),
    )
    call = spawner.calls[0]
    argv = call["argv"]
    # WebSearch is allowed (and ONLY WebSearch — no Write/Edit).
    assert WEB_SEARCH_TOOLS == ["WebSearch"]
    flag_idx = argv.index("--allowedTools")
    following = argv[flag_idx + 1 : flag_idx + 1 + len(WEB_SEARCH_TOOLS)]
    assert following == list(WEB_SEARCH_TOOLS)
    assert "Write" not in argv
    assert "Edit" not in argv
    assert "MultiEdit" not in argv
    # WebFetch stays denied (SSRF guard M-2).
    dis_idx = argv.index("--disallowedTools")
    assert "WebFetch" in argv[dis_idx + 1 :]
    # No --add-dir on the WIKI tree; neutral cwd (not the WIKI dir).
    assert str(wiki) not in argv
    assert call["cwd"] != str(wiki)
    # dontAsk preserved.
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"


async def test_run_captures_permission_denials(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """aisw-t6w Fix 2: non-empty permission_denials surface on the result (not silent ok)."""
    spawner = FakeSpawner(lines=_make_lines_with_denials(), exit_code=0)
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()
    result = await run_wiki_session(
        wiki_id="Medical-WIKI",
        wiki_path=tmp_path / "Medical-WIKI",
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-deny",
        correlation_id="corr-deny",
        runtime_dir=tmp_path / "runtime",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir),
    )
    assert result.exit_code == 0
    assert len(result.permission_denials) == 2
    assert {d["tool_name"] for d in result.permission_denials} == {"Edit", "Write"}


async def test_run_no_denials_default_empty(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """aisw-t6w: a clean run exposes an empty permission_denials list."""
    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()
    result = await run_wiki_session(
        wiki_id="Medical-WIKI",
        wiki_path=tmp_path / "Medical-WIKI",
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-clean",
        correlation_id="corr-clean",
        runtime_dir=tmp_path / "runtime",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_cfg(cfg_dir),
    )
    assert result.permission_denials == []
