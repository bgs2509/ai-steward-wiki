from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_steward_wiki.llm.codex import CodexEvent, CodexRequest, CodexRunKind
from ai_steward_wiki.llm.failover import FailoverPolicy, ProviderState, ReplayBlockedError
from ai_steward_wiki.wiki.runner import (
    WEB_SEARCH_TOOLS,
    WRITE_TOOLS,
    WikiRunnerError,
    _RunConfig,
    run_wiki_session,
)
from ai_steward_wiki.wiki.streaming import StreamEvent
from tests.unit.wiki.conftest import FakeAcquirer, FakeSpawner


class StubCodex:
    complex_model = "gpt-5.5"
    complex_reasoning = "medium"

    def __init__(
        self,
        neutral_cwd: Path,
        *,
        lock_acquirer: FakeAcquirer | None = None,
    ) -> None:
        self.neutral_cwd = neutral_cwd
        self.lock_acquirer = lock_acquirer
        self.calls: list[CodexRequest] = []

    async def run_agent(self, request: CodexRequest) -> list[CodexEvent]:
        if self.lock_acquirer is not None:
            assert self.lock_acquirer.active
        self.calls.append(request)
        return [
            CodexEvent(type="assistant_chunk", payload={"text": "codex answer"}),
            CodexEvent(
                type="final",
                payload={"type": "turn.completed", "usage": {"output_tokens": 2}},
            ),
        ]


def _limit_line() -> bytes:
    return (
        json.dumps(
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "api_error_status": 429,
                "result": "subscription limit reached",
            }
        ).encode()
        + b"\n"
    )


def _write_line() -> bytes:
    return (
        json.dumps(
            {
                "type": "tool_use",
                "name": "Write",
                "input": {"file_path": "log.md"},
            }
        ).encode()
        + b"\n"
    )


def _unknown_tool_line() -> bytes:
    return (
        json.dumps(
            {
                "type": "tool_use",
                "name": "CustomTool",
                "input": {},
            }
        ).encode()
        + b"\n"
    )


def _assistant_line() -> bytes:
    return json.dumps({"type": "assistant", "text": "partial"}).encode() + b"\n"


def _config(
    tmp_path: Path,
    *,
    policy: FailoverPolicy,
    codex: StubCodex,
    allowed_tools: list[str] | None = None,
    web_search: bool = False,
) -> _RunConfig:
    return _RunConfig(
        claude_config_dir=tmp_path / "claude-config",
        timeout_s=2.0,
        term_grace_s=0.1,
        allowed_tools=allowed_tools,
        web_search=web_search,
        failover_policy=policy,
        codex_adapter=codex,  # type: ignore[arg-type]
    )


async def _run(
    *,
    tmp_path: Path,
    prompts_dir: Path,
    acquirer: FakeAcquirer,
    spawner: FakeSpawner,
    config: _RunConfig,
    on_event=None,
    media_paths: list[Path] | None = None,
    extra_add_dirs: list[Path] | None = None,
):
    return await run_wiki_session(
        wiki_id="Health-WIKI",
        wiki_path=tmp_path / "Health-WIKI",
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="run-failover",
        correlation_id="corr-failover",
        runtime_dir=tmp_path / "runtime",
        acquirer=acquirer,
        spawner=spawner,
        config=config,
        on_event=on_event,
        user_input="user query",
        media_paths=media_paths,
        extra_add_dirs=extra_add_dirs,
    )


async def test_safe_limit_runs_codex_under_same_lock(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    policy = FailoverPolicy(cooldown_s=900.0)
    codex = StubCodex(
        tmp_path / "codex-runtime",
        lock_acquirer=fake_acquirer,
    )
    spawner = FakeSpawner(lines=[_limit_line()], exit_code=1)

    result = await _run(
        tmp_path=tmp_path,
        prompts_dir=prompts_dir,
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_config(tmp_path, policy=policy, codex=codex),
    )

    assert [event.type for event in result.events] == ["assistant_chunk", "final"]
    assert fake_acquirer.calls == [("Health-WIKI", tmp_path / "Health-WIKI")]
    assert not fake_acquirer.active
    assert len(codex.calls) == 1
    assert policy.state is ProviderState.CODEX
    transcript = result.transcript_path.read_text(encoding="utf-8")
    assert "subscription limit reached" in transcript
    assert "codex answer" in transcript


async def test_write_before_limit_blocks_codex_replay(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    policy = FailoverPolicy(cooldown_s=900.0)
    codex = StubCodex(tmp_path / "codex-runtime")
    spawner = FakeSpawner(lines=[_write_line(), _limit_line()], exit_code=1)

    with pytest.raises(ReplayBlockedError):
        await _run(
            tmp_path=tmp_path,
            prompts_dir=prompts_dir,
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_config(
                tmp_path,
                policy=policy,
                codex=codex,
                allowed_tools=list(WRITE_TOOLS),
            ),
        )

    assert codex.calls == []
    assert fake_acquirer.calls == [("Health-WIKI", tmp_path / "Health-WIKI")]


async def test_streamed_output_before_limit_blocks_codex_replay(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    policy = FailoverPolicy(cooldown_s=900.0)
    codex = StubCodex(tmp_path / "codex-runtime")
    spawner = FakeSpawner(lines=[_assistant_line(), _limit_line()], exit_code=1)
    delivered: list[StreamEvent] = []

    async def on_event(event: StreamEvent) -> None:
        delivered.append(event)

    with pytest.raises(ReplayBlockedError):
        await _run(
            tmp_path=tmp_path,
            prompts_dir=prompts_dir,
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_config(tmp_path, policy=policy, codex=codex),
            on_event=on_event,
        )

    assert delivered[0].type == "assistant_chunk"
    assert codex.calls == []


async def test_unknown_tool_before_limit_blocks_codex_replay(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    policy = FailoverPolicy(cooldown_s=900.0)
    codex = StubCodex(tmp_path / "codex-runtime")
    spawner = FakeSpawner(lines=[_unknown_tool_line(), _limit_line()], exit_code=1)

    with pytest.raises(ReplayBlockedError):
        await _run(
            tmp_path=tmp_path,
            prompts_dir=prompts_dir,
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_config(tmp_path, policy=policy, codex=codex),
        )

    assert codex.calls == []


async def test_callback_failure_before_limit_blocks_codex_replay(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    policy = FailoverPolicy(cooldown_s=900.0)
    codex = StubCodex(tmp_path / "codex-runtime")
    spawner = FakeSpawner(lines=[_assistant_line(), _limit_line()], exit_code=1)

    async def failing_callback(_event: StreamEvent) -> None:
        raise RuntimeError("delivery failed")

    with pytest.raises(ReplayBlockedError):
        await _run(
            tmp_path=tmp_path,
            prompts_dir=prompts_dir,
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_config(tmp_path, policy=policy, codex=codex),
            on_event=failing_callback,
        )

    assert codex.calls == []


async def test_generic_claude_failure_does_not_run_codex(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    policy = FailoverPolicy(cooldown_s=900.0)
    codex = StubCodex(tmp_path / "codex-runtime")
    spawner = FakeSpawner(lines=[], stderr_bytes=b"boom", exit_code=1)

    with pytest.raises(WikiRunnerError, match="rc=1"):
        await _run(
            tmp_path=tmp_path,
            prompts_dir=prompts_dir,
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_config(tmp_path, policy=policy, codex=codex),
        )

    assert codex.calls == []
    assert policy.state is ProviderState.CLAUDE


async def test_cancellation_propagates_and_releases_lock(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
) -> None:
    policy = FailoverPolicy(cooldown_s=900.0)
    codex = StubCodex(tmp_path / "codex-runtime")
    spawner = FakeSpawner(lines=[], exit_code=0, hang=True)
    task = asyncio.create_task(
        _run(
            tmp_path=tmp_path,
            prompts_dir=prompts_dir,
            acquirer=fake_acquirer,
            spawner=spawner,
            config=_config(tmp_path, policy=policy, codex=codex),
        )
    )
    for _ in range(100):
        if spawner.calls:
            break
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert spawner.calls
    assert not fake_acquirer.active
    assert codex.calls == []
    assert (tmp_path / "Health-WIKI/runs/run-failover/transcript.jsonl").exists()


@pytest.mark.parametrize(
    ("allowed_tools", "web_search", "expected_kind"),
    [
        (None, False, CodexRunKind.AGENT_READ),
        (list(WRITE_TOOLS), False, CodexRunKind.AGENT_WRITE),
        (list(WEB_SEARCH_TOOLS), True, CodexRunKind.WEB),
    ],
)
async def test_codex_capability_mapping(
    tmp_path: Path,
    prompts_dir: Path,
    fake_acquirer: FakeAcquirer,
    allowed_tools: list[str] | None,
    web_search: bool,
    expected_kind: CodexRunKind,
) -> None:
    policy = FailoverPolicy(cooldown_s=900.0)
    codex = StubCodex(tmp_path / "codex-runtime")
    spawner = FakeSpawner(lines=[_limit_line()], exit_code=1)
    media = tmp_path / "media" / "photo.jpg"
    media.parent.mkdir()
    media.write_bytes(b"image")
    extra = tmp_path / "Money-WIKI"
    extra.mkdir()

    await _run(
        tmp_path=tmp_path,
        prompts_dir=prompts_dir,
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_config(
            tmp_path,
            policy=policy,
            codex=codex,
            allowed_tools=allowed_tools,
            web_search=web_search,
        ),
        media_paths=None if web_search else [media],
        extra_add_dirs=None if web_search else [extra],
    )

    request = codex.calls[0]
    assert request.run_kind is expected_kind
    if expected_kind is CodexRunKind.AGENT_WRITE:
        assert request.cwd == tmp_path / "Health-WIKI"
        assert request.writable_wiki == tmp_path / "Health-WIKI"
    else:
        assert request.cwd == codex.neutral_cwd
        assert request.writable_wiki is None
    if web_search:
        assert request.image_paths == ()
        assert "WORKSPACE_ROOT" not in request.prompt
    else:
        assert request.image_paths == (media,)
        assert str(extra) in request.prompt
