"""Wiki runner AsyncioSpawner emits claude_cli.spawn at the boundary.

The exit/error counterparts are emitted by run_wiki_session adjacent to the
existing wiki.run.finish / wiki.run.error logs; covered by integration tests
of run_wiki_session, not exercised here (this test isolates the Spawner).
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from structlog.testing import capture_logs

from ai_steward_wiki.wiki.runner import AsyncioSpawner


def _python_argv(code: str) -> list[str]:
    py = shutil.which(sys.executable) or sys.executable
    return [py, "-c", code]


def test_spawn_emits_claude_cli_spawn(tmp_path: Path) -> None:
    spawner = AsyncioSpawner()
    argv = _python_argv("print('x')")

    async def _run() -> None:
        proc = await spawner.spawn(argv, env={"PATH": "/usr/bin:/bin"}, cwd=tmp_path)
        await proc.wait()

    with capture_logs() as logs:
        asyncio.run(_run())
    spawn = next(r for r in logs if r["event"] == "claude_cli.spawn")
    assert spawn["argv_length"] == len(argv)
    assert spawn["env_keys_count"] == 1
    assert spawn["cwd"] == str(tmp_path)
    # PII: no argv items or env keys/values.
    assert sys.executable not in repr(spawn)
    assert "PATH" not in repr(spawn)
