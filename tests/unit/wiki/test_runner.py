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
    wiki = tmp_path / "Health-WIKI"
    runtime = tmp_path / "runtime"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()

    result = await run_wiki_session(
        wiki_id="Health-WIKI",
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
    # FR-3: cwd is the neutral claude_config_dir, not the wiki path.
    assert spawner.calls[0]["cwd"] == str(cfg_dir)
    # env: CLAUDE_CONFIG_DIR + minimal PATH.
    env = spawner.calls[0]["env"]
    assert isinstance(env, dict)
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg_dir)
    assert env["PATH"] == "/usr/bin:/bin"
    assert fake_acquirer.calls == [("Health-WIKI", wiki)]
    # aisw-w83: when user_input is empty, stdin_data is None (DEVNULL on the
    # real spawner path); the runner does not synthesize input.
    assert spawner.calls[0]["stdin_data"] is None


async def test_run_wiki_session_pipes_user_input_to_stdin(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    """aisw-w83: user_input is delivered to claude via stdin, not the system prompt."""
    spawner = FakeSpawner(lines=_make_lines(), exit_code=0)
    wiki = tmp_path / "Health-WIKI"
    runtime = tmp_path / "runtime"
    cfg_dir = tmp_path / "claude-config"
    cfg_dir.mkdir()

    await run_wiki_session(
        wiki_id="Health-WIKI",
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
    wiki = tmp_path / "Health-WIKI"
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
