from __future__ import annotations

import asyncio
import sys
import time

import pytest

from ai_steward_wiki.scheduler.core import build_scheduler, kill_with_sequence


def test_build_scheduler_returns_asyncioscheduler(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    sched = build_scheduler(f"sqlite:///{db_path}")
    assert sched.__class__.__name__ == "AsyncIOScheduler"


async def test_kill_sequence_terminates_within_grace() -> None:
    """SIGTERM-respecting child should exit within grace, not SIGKILL."""
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
    start = time.monotonic()
    rc = await kill_with_sequence(proc, grace_seconds=2.0)
    elapsed = time.monotonic() - start
    assert rc != 0  # killed by signal
    assert elapsed < 1.5  # fast SIGTERM path


async def test_kill_sequence_falls_back_to_sigkill() -> None:
    """Child that ignores SIGTERM must be SIGKILL'd after grace."""
    code = (
        "import signal, sys, time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "sys.stdout.write('ready\\n'); sys.stdout.flush();"
        "time.sleep(30)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE
    )
    assert proc.stdout is not None
    await proc.stdout.readline()  # wait for child to install SIGTERM handler
    start = time.monotonic()
    rc = await kill_with_sequence(proc, grace_seconds=0.4)
    elapsed = time.monotonic() - start
    assert rc != 0
    assert 0.35 <= elapsed < 3.0


@pytest.mark.parametrize("grace", [0.1, 0.5])
async def test_kill_already_exited_is_safe(grace: float) -> None:
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    await proc.wait()
    rc = await kill_with_sequence(proc, grace_seconds=grace)
    assert rc == 0
