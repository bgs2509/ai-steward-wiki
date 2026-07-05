from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

import ai_steward_wiki.llm as llm_package
from ai_steward_wiki.llm.codex import (
    AsyncioCodexSpawner,
    CodexCliAdapter,
    CodexEvent,
    CodexOutputError,
    CodexRequest,
    CodexRunKind,
    CodexUnavailableError,
    ProcessResult,
    normalize_codex_event,
)
from ai_steward_wiki.llm.failover import EvidenceKind


class StubSpawner:
    def __init__(self, results: Sequence[ProcessResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []
        self.schemas: list[dict[str, object]] = []

    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        stdin: bytes,
        timeout_s: float,
        cwd: Path,
    ) -> ProcessResult:
        self.calls.append(
            {
                "argv": argv,
                "env": env,
                "stdin": stdin,
                "timeout_s": timeout_s,
                "cwd": cwd,
            }
        )
        if "--output-schema" in argv:
            schema_path = Path(argv[argv.index("--output-schema") + 1])
            self.schemas.append(json.loads(schema_path.read_text(encoding="utf-8")))
        return self._results.pop(0)


class FailingSecondSpawner(StubSpawner):
    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        stdin: bytes,
        timeout_s: float,
        cwd: Path,
    ) -> ProcessResult:
        if self.calls:
            raise CodexUnavailableError("login command could not start")
        return await super().spawn(
            argv,
            env=env,
            stdin=stdin,
            timeout_s=timeout_s,
            cwd=cwd,
        )


class HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.killed = False

    async def communicate(self, _stdin: bytes) -> tuple[bytes, bytes]:
        self.started.set()
        await self.release.wait()
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.release.set()

    async def wait(self) -> int:
        return self.returncode or 0


def make_adapter(
    tmp_path: Path,
    spawner: StubSpawner,
    *,
    binary: str = "/opt/bin/codex",
) -> CodexCliAdapter:
    codex_home = tmp_path / "codex-home"
    neutral_cwd = tmp_path / "runtime"
    codex_home.mkdir(exist_ok=True)
    neutral_cwd.mkdir(exist_ok=True)
    return CodexCliAdapter(
        binary=binary,
        expected_version="0.142.5",
        codex_home=codex_home,
        neutral_cwd=neutral_cwd,
        light_model="gpt-5.4-mini",
        light_reasoning="low",
        complex_model="gpt-5.5",
        complex_reasoning="medium",
        spawner=spawner,
    )


def structured_request(tmp_path: Path) -> CodexRequest:
    return CodexRequest(
        prompt="classify this",
        model="gpt-5.4-mini",
        reasoning="low",
        run_kind=CodexRunKind.STRUCTURED,
        correlation_id="corr-1",
        timeout_s=30.0,
        cwd=tmp_path / "runtime",
        output_schema={"type": "object", "additionalProperties": True},
    )


def test_structured_argv_is_fully_explicit(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path, StubSpawner([]))
    schema_path = tmp_path / "schema.json"

    argv = adapter.build_argv(structured_request(tmp_path), output_schema_path=schema_path)

    assert argv[0] == "/opt/bin/codex"
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert "--strict-config" in argv
    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("--model") + 1] == "gpt-5.4-mini"
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--cd") + 1] == str(tmp_path / "runtime")
    assert 'model_reasoning_effort="low"' in argv
    assert 'approval_policy="never"' in argv
    assert "project_doc_max_bytes=0" in argv
    assert argv[-1] == "-"


def test_write_argv_grants_only_selected_wiki(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path, StubSpawner([]))
    selected = tmp_path / "selected-WIKI"
    additional = tmp_path / "other-WIKI"
    image = tmp_path / "photo.jpg"
    request = CodexRequest(
        prompt="update wiki",
        model="gpt-5.5",
        reasoning="medium",
        run_kind=CodexRunKind.AGENT_WRITE,
        correlation_id="corr-2",
        timeout_s=300.0,
        cwd=selected,
        writable_wiki=selected,
        readable_paths=(additional,),
        image_paths=(image,),
    )

    argv = adapter.build_argv(request)

    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("--cd") + 1] == str(selected)
    assert "--add-dir" not in argv
    assert str(additional) not in argv
    assert argv[argv.index("--image") + 1] == str(image)
    assert "--json" in argv


def test_web_argv_has_search_without_wiki_access(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path, StubSpawner([]))
    request = CodexRequest(
        prompt="search",
        model="gpt-5.5",
        reasoning="medium",
        run_kind=CodexRunKind.WEB,
        correlation_id="corr-3",
        timeout_s=300.0,
        cwd=tmp_path / "runtime",
    )

    argv = adapter.build_argv(request)

    assert argv.index("--search") < argv.index("exec")
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--add-dir" not in argv
    assert "--json" in argv


def test_environment_is_subscription_only(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path, StubSpawner([]))

    env = adapter.build_env()

    assert env == {
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    assert "OPENAI_API_KEY" not in env


def test_llm_package_exports_codex_contract() -> None:
    assert llm_package.CodexCliAdapter is CodexCliAdapter
    assert llm_package.CodexEvent is CodexEvent
    assert llm_package.CodexRequest is CodexRequest
    assert llm_package.CodexRunKind is CodexRunKind


async def test_run_structured_parses_object_and_writes_schema(tmp_path: Path) -> None:
    spawner = StubSpawner(
        [ProcessResult(exit_code=0, stdout=b'{"intent":"WIKI_QUERY"}\n', stderr=b"")]
    )
    adapter = make_adapter(tmp_path, spawner)

    result = await adapter.run_structured(structured_request(tmp_path))

    assert result == {"intent": "WIKI_QUERY"}
    assert spawner.schemas == [{"type": "object", "additionalProperties": True}]
    assert spawner.calls[0]["stdin"] == b"classify this"


async def test_run_structured_rejects_non_object(tmp_path: Path) -> None:
    spawner = StubSpawner([ProcessResult(exit_code=0, stdout=b"[]", stderr=b"")])
    adapter = make_adapter(tmp_path, spawner)

    with pytest.raises(CodexOutputError, match="object"):
        await adapter.run_structured(structured_request(tmp_path))


async def test_run_text_returns_trimmed_stdout(tmp_path: Path) -> None:
    spawner = StubSpawner([ProcessResult(exit_code=0, stdout=b" result \n", stderr=b"")])
    adapter = make_adapter(tmp_path, spawner)
    request = CodexRequest(
        prompt="summarize",
        model="gpt-5.5",
        reasoning="medium",
        run_kind=CodexRunKind.TEXT,
        correlation_id="corr-4",
        timeout_s=30.0,
        cwd=tmp_path / "runtime",
    )

    assert await adapter.run_text(request) == "result"


@pytest.mark.parametrize(
    ("item_type", "event_type", "evidence_kind"),
    [
        ("agent_message", "assistant_chunk", EvidenceKind.READ_ONLY),
        ("file_change", "tool_use", EvidenceKind.MUTATION),
        ("web_search", "tool_use", EvidenceKind.READ_ONLY),
        ("command_execution", "tool_use", EvidenceKind.UNKNOWN),
    ],
)
def test_normalize_codex_completed_items(
    item_type: str,
    event_type: str,
    evidence_kind: EvidenceKind,
) -> None:
    raw = {
        "type": "item.completed",
        "item": {"type": item_type, "text": "answer", "command": "custom-command"},
    }

    event, evidence = normalize_codex_event(raw)

    assert event.type == event_type
    assert evidence.kind is evidence_kind


def test_normalize_codex_turn_completed_preserves_usage() -> None:
    event, evidence = normalize_codex_event(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 11, "cached_input_tokens": 3, "output_tokens": 7},
        }
    )

    assert event.type == "final"
    assert event.payload["usage"]["output_tokens"] == 7
    assert evidence.kind is EvidenceKind.READ_ONLY


def test_normalize_codex_failure_raises_typed_error() -> None:
    with pytest.raises(CodexOutputError, match="failed"):
        normalize_codex_event({"type": "turn.failed", "error": {"message": "bad"}})


async def test_run_agent_parses_jsonl(tmp_path: Path) -> None:
    lines = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]
    stdout = b"\n".join(json.dumps(line).encode() for line in lines)
    spawner = StubSpawner([ProcessResult(exit_code=0, stdout=stdout, stderr=b"")])
    adapter = make_adapter(tmp_path, spawner)
    request = CodexRequest(
        prompt="read wiki",
        model="gpt-5.5",
        reasoning="medium",
        run_kind=CodexRunKind.AGENT_READ,
        correlation_id="corr-5",
        timeout_s=300.0,
        cwd=tmp_path / "runtime",
    )

    events = await adapter.run_agent(request)

    assert [event.type for event in events] == ["assistant_chunk", "final"]


async def test_nonzero_exit_is_unavailable_without_stderr_leak(tmp_path: Path) -> None:
    spawner = StubSpawner([ProcessResult(exit_code=1, stdout=b"", stderr=b"secret auth payload")])
    adapter = make_adapter(tmp_path, spawner)

    with pytest.raises(CodexUnavailableError) as captured:
        await adapter.run_text(
            CodexRequest(
                prompt="text",
                model="gpt-5.5",
                reasoning="medium",
                run_kind=CodexRunKind.TEXT,
                correlation_id="corr-6",
                timeout_s=30.0,
                cwd=tmp_path / "runtime",
            )
        )

    assert "secret" not in str(captured.value)


async def test_readiness_uses_only_non_model_commands(tmp_path: Path) -> None:
    binary = tmp_path / "codex"
    binary.write_text("stub", encoding="utf-8")
    binary.chmod(0o755)
    spawner = StubSpawner(
        [
            ProcessResult(0, b"codex-cli 0.142.5\n", b""),
            ProcessResult(0, b"Logged in using ChatGPT\n", b""),
            ProcessResult(
                0,
                b"--ephemeral --ignore-user-config --ignore-rules --strict-config "
                b"--json --output-schema --sandbox --model --cd --add-dir\n",
                b"",
            ),
        ]
    )
    adapter = make_adapter(tmp_path, spawner, binary=str(binary))

    readiness = await adapter.check_readiness()

    assert readiness.ready is True
    assert [call["argv"] for call in spawner.calls] == [
        [str(binary), "--version"],
        [str(binary), "login", "status"],
        [str(binary), "exec", "--help"],
    ]


async def test_readiness_rejects_version_mismatch(tmp_path: Path) -> None:
    binary = tmp_path / "codex"
    binary.write_text("stub", encoding="utf-8")
    binary.chmod(0o755)
    spawner = StubSpawner([ProcessResult(0, b"codex-cli 0.142.4\n", b"")])
    adapter = make_adapter(tmp_path, spawner, binary=str(binary))

    readiness = await adapter.check_readiness()

    assert readiness.ready is False
    assert readiness.reason == "version_mismatch"


async def test_readiness_rejects_missing_login(tmp_path: Path) -> None:
    binary = tmp_path / "codex"
    binary.write_text("stub", encoding="utf-8")
    binary.chmod(0o755)
    spawner = StubSpawner(
        [
            ProcessResult(0, b"codex-cli 0.142.5\n", b""),
            ProcessResult(1, b"", b"not logged in"),
        ]
    )
    adapter = make_adapter(tmp_path, spawner, binary=str(binary))

    readiness = await adapter.check_readiness()

    assert readiness.ready is False
    assert readiness.reason == "authentication_unavailable"


async def test_readiness_rejects_api_key_login_mode(tmp_path: Path) -> None:
    binary = tmp_path / "codex"
    binary.write_text("stub", encoding="utf-8")
    binary.chmod(0o755)
    spawner = StubSpawner(
        [
            ProcessResult(0, b"codex-cli 0.142.5\n", b""),
            ProcessResult(0, b"Logged in using an API key\n", b""),
        ]
    )
    adapter = make_adapter(tmp_path, spawner, binary=str(binary))

    readiness = await adapter.check_readiness()

    assert readiness.ready is False
    assert readiness.reason == "subscription_auth_required"


async def test_readiness_converts_login_invocation_failure(tmp_path: Path) -> None:
    binary = tmp_path / "codex"
    binary.write_text("stub", encoding="utf-8")
    binary.chmod(0o755)
    spawner = FailingSecondSpawner([ProcessResult(0, b"codex-cli 0.142.5\n", b"")])
    adapter = make_adapter(tmp_path, spawner, binary=str(binary))

    readiness = await adapter.check_readiness()

    assert readiness.ready is False
    assert readiness.reason == "invocation_failed"


async def test_asyncio_spawner_wraps_spawn_oserror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def failed_spawn(*_args: object, **_kwargs: object) -> HangingProcess:
        raise OSError("secret executable path")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failed_spawn)

    with pytest.raises(CodexUnavailableError) as captured:
        await AsyncioCodexSpawner().spawn(
            ["codex", "--version"],
            env={},
            stdin=b"",
            timeout_s=1.0,
            cwd=tmp_path,
        )

    assert "secret" not in str(captured.value)


async def test_asyncio_spawner_kills_timed_out_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = HangingProcess()

    async def fake_spawn(*_args: object, **_kwargs: object) -> HangingProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    with pytest.raises(CodexUnavailableError, match="timed out"):
        await AsyncioCodexSpawner().spawn(
            ["codex", "exec"],
            env={},
            stdin=b"prompt",
            timeout_s=0.01,
            cwd=tmp_path,
        )

    assert process.killed is True


async def test_asyncio_spawner_kills_cancelled_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = HangingProcess()

    async def fake_spawn(*_args: object, **_kwargs: object) -> HangingProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    task = asyncio.create_task(
        AsyncioCodexSpawner().spawn(
            ["codex", "exec"],
            env={},
            stdin=b"prompt",
            timeout_s=60.0,
            cwd=tmp_path,
        )
    )
    await process.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed is True
