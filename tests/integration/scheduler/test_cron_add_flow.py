"""End-to-end smoke test for the /cron_add → producer → queue → consumer
walking-skeleton flow (aisw-02v).

Gated by RUN_INTEGRATION=1 since it touches an in-memory SQLite jobs.db and
exercises the full producer/consumer wiring with a stub subprocess. It does
NOT spawn systemd-run or the real Claude CLI — those are out of scope for a
unit-test machine. Real CLI is exercised by tests/integration/classifier/.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler import cron_user as cron_user_mod
from ai_steward_wiki.scheduler.consumer import CronConsumer
from ai_steward_wiki.scheduler.cron_user import (
    create_cron_user_job,
    fire_cron_user_job,
)
from ai_steward_wiki.scheduler.queue import PriorityJobQueue
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 to enable",
)


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> None:
        self.sent.append((chat_id, text))


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    async def wait(self) -> int:
        return self.returncode


class _StubSpawner:
    def __init__(self, proc: _FakeProc) -> None:
        self._proc = proc
        self.argv: list[str] | None = None

    async def spawn(self, argv, *, cwd, env):
        self.argv = list(argv)
        return self._proc


@pytest.fixture
async def session_factory(tmp_path: Any):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_cron_user_ctx():
    cron_user_mod._ctx = None
    yield
    cron_user_mod._ctx = None


async def test_full_flow_producer_to_consumer_to_bot(session_factory, tmp_path: Path) -> None:
    """create_cron_user_job → fire_cron_user_job → consumer → bot.send_message."""
    queue = PriorityJobQueue()
    scheduler = AsyncIOScheduler()
    cron_user_mod.set_cron_user_context(scheduler, queue, session_factory)
    scheduler.start()
    try:
        rec = Recurrence(kind="daily", time_hhmm="00:00", tz="UTC")
        # 1. /cron_add equivalent — persist Job + register CronTrigger.
        job_id = await create_cron_user_job(
            owner_telegram_id=100,
            chat_id=100,
            recurrence=rec,
            command="echo aisw-02v",
            user_tz="UTC",
            wiki_id=None,
        )
        # 2. Skip the wall-clock wait — invoke the firing callback directly
        #    (this is exactly what APScheduler would do at the cron moment).
        await fire_cron_user_job(job_id)
        # 3. Pull the message off the real queue.
        item = await asyncio.wait_for(queue.get(), 1.0)
        assert item.lane == 2  # Lane.CRON_WRITE.value

        # 4. Build a consumer pointing at a stub Spawner.
        bot = _FakeBot()
        prompt = tmp_path / "prompt.md"
        prompt.write_text("system prompt", encoding="utf-8")
        stub = _StubSpawner(_FakeProc(stdout=b"hello from aisw-02v"))
        consumer = CronConsumer(
            queue=queue,
            bot=bot,  # type: ignore[arg-type]
            claude_binary="/usr/bin/echo",
            claude_config_dir=tmp_path,
            prompt_path=prompt,
            jobs_session_maker=session_factory,
            timeout_s=10.0,
            spawner=stub,
        )
        await consumer._execute_one(item.payload)

        # 5. Bot received the stdout.
        assert len(bot.sent) == 1
        chat_id, text = bot.sent[0]
        assert chat_id == 100
        assert "hello from aisw-02v" in text

        # 6. argv runs the Claude binary directly — no systemd-run wrap (aisw-abc).
        assert stub.argv is not None
        assert stub.argv[0] == "/usr/bin/echo"
        assert "systemd-run" not in stub.argv
        assert "--scope" not in stub.argv
        assert "--collect" not in stub.argv
        assert stub.argv[-1] == "echo aisw-02v"

        # 7. Job row reached terminal 'finished' status.
        async with session_factory() as s:
            row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
        assert row.status == "finished"
        assert row.finished_at_utc is not None
        assert row.started_at_utc is not None
        # started_at_utc precedes finished_at_utc.
        assert row.started_at_utc <= row.finished_at_utc
        # Trigger registered.
        triggers = [j.id for j in scheduler.get_jobs()]
        assert f"cron_user:{job_id}" in triggers
        # Sanity: the row's payload still has the new shape.
        assert row.payload["command"] == "echo aisw-02v"
        assert row.payload["recurrence"]["kind"] == "daily"
        # Idempotency: a second fire on the now-'finished' row is a no-op.
        await fire_cron_user_job(job_id)
        assert queue.qsize() == 0
        # Audit: the row's lifecycle ts is monotonically UTC-aware-stripped.
        assert isinstance(row.created_at_utc, datetime)
    finally:
        scheduler.shutdown(wait=False)
