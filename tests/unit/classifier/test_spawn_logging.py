"""Claude CLI spawn logging — claude_cli.{spawn,exit,error} on AsyncioSpawner.

Uses real Python subprocess via `python -c '...'` for deterministic exit codes;
no asyncio-internals mocking.
"""

from __future__ import annotations

import asyncio
import shutil
import sys

import pytest
from structlog.testing import capture_logs

from ai_steward_wiki.classifier.backend import AsyncioSpawner
from ai_steward_wiki.classifier.schema import ClassifierTimeoutError


def _python_argv(code: str) -> list[str]:
    py = shutil.which(sys.executable) or sys.executable
    return [py, "-c", code]


def test_spawn_logs_spawn_and_exit_on_success() -> None:
    spawner = AsyncioSpawner()
    argv = _python_argv("print('hello')")
    with capture_logs() as logs:
        rc, stdout, stderr = asyncio.run(
            spawner.spawn(argv, env={"PATH": "/usr/bin:/bin"}, stdin=b"", timeout_s=10.0)
        )
    assert rc == 0
    assert b"hello" in stdout
    events = {r["event"] for r in logs}
    assert "claude_cli.spawn" in events
    assert "claude_cli.exit" in events
    assert "claude_cli.error" not in events
    spawn = next(r for r in logs if r["event"] == "claude_cli.spawn")
    assert spawn["argv_length"] == len(argv)
    assert spawn["env_keys_count"] == 1
    assert spawn["cwd"] is None
    # PII: no argv items, no env keys/values in the spawn record.
    spawn_repr = repr(spawn)
    assert sys.executable not in spawn_repr
    assert "PATH" not in spawn_repr
    exit_rec = next(r for r in logs if r["event"] == "claude_cli.exit")
    assert exit_rec["exit_code"] == 0
    assert isinstance(exit_rec["duration_ms"], int)
    assert exit_rec["stdout_bytes"] == len(stdout)
    assert exit_rec["stderr_bytes"] == len(stderr)
    # PII: no stdout/stderr content.
    assert "hello" not in repr(exit_rec)


def test_spawn_logs_error_on_nonzero_exit() -> None:
    spawner = AsyncioSpawner()
    argv = _python_argv("import sys; sys.exit(7)")
    with capture_logs() as logs:
        rc, _stdout, _stderr = asyncio.run(
            spawner.spawn(argv, env={"PATH": "/usr/bin:/bin"}, stdin=b"", timeout_s=10.0)
        )
    assert rc == 7
    err = next(r for r in logs if r["event"] == "claude_cli.error")
    assert err["log_level"] == "error"
    assert err["exit_code"] == 7
    assert err["reason"] == "nonzero_exit"
    # .exit still fires alongside the .error
    assert any(r["event"] == "claude_cli.exit" for r in logs)


def test_spawn_logs_error_on_timeout() -> None:
    spawner = AsyncioSpawner()
    argv = _python_argv("import time; time.sleep(5)")
    with capture_logs() as logs, pytest.raises(ClassifierTimeoutError):
        asyncio.run(spawner.spawn(argv, env={"PATH": "/usr/bin:/bin"}, stdin=b"", timeout_s=0.2))
    err = next(r for r in logs if r["event"] == "claude_cli.error")
    assert err["reason"] == "timeout"
    assert err["exit_code"] is None
    assert isinstance(err["duration_ms"], int)
