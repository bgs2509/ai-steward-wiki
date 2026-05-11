from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ai_steward_wiki.wiki.runner import SpawnedProcess


@dataclass
class FakeProcess:
    """Minimal SpawnedProcess test double backed by an in-memory StreamReader."""

    pid: int = 4242
    stdin: asyncio.StreamWriter | None = None
    stdout: asyncio.StreamReader | None = None
    stderr: asyncio.StreamReader | None = None
    _exit_code: int = 0
    _terminated: bool = False
    _killed: bool = False
    _hang: bool = False

    async def wait(self) -> int:
        if self._hang and not (self._terminated or self._killed):
            # Block indefinitely until terminate/kill is called.
            while not (self._terminated or self._killed):
                await asyncio.sleep(0.01)
        return self._exit_code

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._killed = True


@dataclass
class FakeSpawner:
    """Records argv/env/cwd, returns a FakeProcess with pre-loaded stdout lines."""

    lines: list[bytes] = field(default_factory=list)
    stderr_bytes: bytes = b""
    exit_code: int = 0
    hang: bool = False
    calls: list[dict[str, object]] = field(default_factory=list)

    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        stdin_data: bytes | None = None,
    ) -> SpawnedProcess:
        self.calls.append(
            {"argv": list(argv), "env": dict(env), "cwd": str(cwd), "stdin_data": stdin_data}
        )
        reader = asyncio.StreamReader()
        for ln in self.lines:
            reader.feed_data(ln)
        if not self.hang:
            reader.feed_eof()
        err = asyncio.StreamReader()
        if self.stderr_bytes:
            err.feed_data(self.stderr_bytes)
        err.feed_eof()
        proc = FakeProcess(
            stdout=reader,
            stderr=err,
            _exit_code=self.exit_code,
            _hang=self.hang,
        )
        return proc  # type: ignore[return-value]


@dataclass
class FakeAcquirer:
    """No-op LockAcquirer recording (wiki_id, wiki_path) on each acquire."""

    calls: list[tuple[str, Path]] = field(default_factory=list)

    def acquire(self, wiki_id: str, wiki_path: Path) -> AbstractAsyncContextManager[None]:
        self.calls.append((wiki_id, wiki_path))

        @asynccontextmanager
        async def _cm() -> AsyncIterator[None]:
            yield

        return _cm()


@pytest.fixture
def fake_spawner() -> FakeSpawner:
    return FakeSpawner()


@pytest.fixture
def fake_acquirer() -> FakeAcquirer:
    return FakeAcquirer()


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "wiki.md").write_text("semver: 1.0.0\n\n# base\n", encoding="utf-8")
    (d / "inbox.md").write_text("semver: 1.0.0\n\n# inbox overlay\n", encoding="utf-8")
    (d / "domain-default.md").write_text("semver: 1.0.0\n\n# default overlay\n", encoding="utf-8")
    return d
