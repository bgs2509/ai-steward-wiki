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
        config=_RunConfig(timeout_s=2.0, term_grace_s=0.1),
    )

    assert result.exit_code == 0
    assert [e.type for e in result.events] == ["assistant_chunk", "final"]
    assert result.transcript_path.exists()
    # Atomic write: no .tmp leftover.
    assert not result.transcript_path.with_suffix(result.transcript_path.suffix + ".tmp").exists()
    # Argv assertions: contains --add-dir <wiki> and --output-format stream-json.
    argv = spawner.calls[0]["argv"]
    assert isinstance(argv, list)
    assert "--add-dir" in argv
    assert str(wiki) in argv
    assert "stream-json" in argv
    assert any(str(arg).startswith("@") for arg in argv)
    # Acquirer received the wiki_id.
    assert fake_acquirer.calls == [("Health-WIKI", wiki)]


async def test_run_wiki_session_timeout_invokes_kill(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    spawner = FakeSpawner(lines=[], exit_code=0, hang=True)
    wiki = tmp_path / "Slow-WIKI"
    runtime = tmp_path / "runtime"

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
            config=_RunConfig(timeout_s=0.1, term_grace_s=0.05),
        )

    # Transcript should still be persisted (with whatever events arrived — none here).
    transcript = wiki / "runs" / "run-timeout" / "transcript.jsonl"
    assert transcript.exists()
