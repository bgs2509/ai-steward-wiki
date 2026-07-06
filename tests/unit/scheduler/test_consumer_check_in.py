# FILE: tests/unit/scheduler/test_consumer_check_in.py
"""RED-first coverage for CronConsumer's check_in branch + ru fallback (aisw-xi8, DEC-8, FR-6)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.scheduler.consumer import CronConsumer
from ai_steward_wiki.scheduler.queue import PriorityJobQueue
from ai_steward_wiki.scheduler.queue_payloads import CheckInQueueMsg
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job


class _FakeProc:
    def __init__(self, *, rc: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = rc
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def terminate(self) -> None: ...
    def kill(self) -> None: ...

    async def wait(self) -> int:
        return self.returncode


class _FakeSpawner:
    def __init__(self, proc: _FakeProc | Exception) -> None:
        self._proc = proc
        self.argv: list[str] | None = None

    async def spawn(self, argv, *, cwd, env):
        self.argv = list(argv)
        if isinstance(self._proc, Exception):
            raise self._proc
        return self._proc


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _insert_job(sm, *, kind: str = "check_in") -> int:
    async with sm() as s, s.begin():
        row = Job(
            owner_telegram_id=1,
            chat_id=99,
            kind=kind,
            status="queued",
            priority=2,
            payload={},
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(row)
        await s.flush()
        return row.id


def _make_consumer(spawner, tmp_path: Path, *, timeout_s: float = 600.0) -> CronConsumer:
    # aisw-xi8 deviation (low-risk, mechanical): the plan's snippet used hardcoded
    # /tmp/cron_user.md and /tmp/check_in.md paths that do not exist on disk;
    # system_prompt_argv() actually reads prompt_path, so a nonexistent path
    # raises FileNotFoundError. Mirrors test_consumer.py's own fake_prompt_file
    # fixture pattern — real files under pytest's tmp_path.
    prompt_path = tmp_path / "cron_user.md"
    prompt_path.write_text("cron-user system prompt", encoding="utf-8")
    check_in_prompt_path = tmp_path / "check_in.md"
    check_in_prompt_path.write_text("check-in system prompt", encoding="utf-8")
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=None)
    return CronConsumer(
        queue=PriorityJobQueue(),
        bot=bot,
        claude_binary="claude",
        claude_config_dir=tmp_path / "cc",
        prompt_path=prompt_path,
        check_in_prompt_path=check_in_prompt_path,
        jobs_session_maker=None,  # set per-test below
        timeout_s=timeout_s,
        spawner=spawner,
    ), bot


async def test_check_in_happy_path_sends_generated_question(
    session_factory, tmp_path: Path
) -> None:
    consumer, bot = _make_consumer(
        _FakeSpawner(_FakeProc(rc=0, stdout="Как прошёл твой день сегодня?".encode())), tmp_path
    )
    consumer._jobs_session_maker = session_factory
    job_id = await _insert_job(session_factory)
    msg = CheckInQueueMsg(
        job_id=job_id,
        owner_telegram_id=1,
        chat_id=99,
        question_topic="как прошёл день",
        correlation_id="c1",
        scheduled_at_utc=datetime.now(UTC),
    )

    await consumer._execute_one(msg)

    bot.send_message.assert_awaited_once_with(99, "Как прошёл твой день сегодня?")
    async with session_factory() as s:
        row = await s.get(Job, job_id)
    assert row.status == "finished"


async def test_check_in_uses_check_in_prompt_path_in_argv(session_factory, tmp_path: Path) -> None:
    spawner = _FakeSpawner(_FakeProc(rc=0, stdout="Вопрос?".encode()))
    consumer, _ = _make_consumer(spawner, tmp_path)
    consumer._jobs_session_maker = session_factory
    job_id = await _insert_job(session_factory)
    msg = CheckInQueueMsg(
        job_id=job_id,
        owner_telegram_id=1,
        chat_id=99,
        question_topic="как прошёл день",
        correlation_id="c1",
        scheduled_at_utc=datetime.now(UTC),
    )
    await consumer._execute_one(msg)
    # aisw-xi8 deviation (low-risk, mechanical): system_prompt_argv() inlines the
    # FILE CONTENT via --system-prompt (claude_cli/common.py, aisw-adj), not the
    # path string — so argv never literally contains "check_in.md". Assert on
    # the check_in-specific prompt CONTENT instead, proving _build_check_in_argv
    # used self._check_in_prompt_path, not self._prompt_path (cron_user's).
    assert any("check-in system prompt" in a for a in (spawner.argv or []))
    assert "как прошёл день" in (spawner.argv or [])[-1]


async def test_check_in_nonzero_exit_sends_deterministic_fallback_not_error(
    session_factory, tmp_path: Path
) -> None:
    consumer, bot = _make_consumer(_FakeSpawner(_FakeProc(rc=1, stderr=b"boom")), tmp_path)
    consumer._jobs_session_maker = session_factory
    job_id = await _insert_job(session_factory)
    msg = CheckInQueueMsg(
        job_id=job_id,
        owner_telegram_id=1,
        chat_id=99,
        question_topic="принимала ли лекарства",
        correlation_id="c1",
        scheduled_at_utc=datetime.now(UTC),
    )

    await consumer._execute_one(msg)

    bot.send_message.assert_awaited_once_with(99, "Хотел спросить: принимала ли лекарства")
    async with session_factory() as s:
        row = await s.get(Job, job_id)
    assert row.status == "finished"  # a delivered fallback is a completed check-in, not a failure


async def test_check_in_timeout_sends_deterministic_fallback(
    session_factory, tmp_path: Path
) -> None:
    import asyncio

    class _HangingProc(_FakeProc):
        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""  # pragma: no cover

    consumer, bot = _make_consumer(_FakeSpawner(_HangingProc(rc=0)), tmp_path, timeout_s=0.01)
    consumer._jobs_session_maker = session_factory
    job_id = await _insert_job(session_factory)
    msg = CheckInQueueMsg(
        job_id=job_id,
        owner_telegram_id=1,
        chat_id=99,
        question_topic="как дела в школе",
        correlation_id="c1",
        scheduled_at_utc=datetime.now(UTC),
    )

    await consumer._execute_one(msg)

    bot.send_message.assert_awaited_once_with(99, "Хотел спросить: как дела в школе")
    async with session_factory() as s:
        row = await s.get(Job, job_id)
    assert row.status == "finished"


async def test_cron_user_kind_still_dispatches_via_parse_queue_msg(
    session_factory, tmp_path: Path
) -> None:
    """Regression: the pre-existing cron_user path is unaffected by the widened union."""
    from ai_steward_wiki.scheduler.queue_payloads import CronUserQueueMsg

    consumer, bot = _make_consumer(_FakeSpawner(_FakeProc(rc=0, stdout=b"done")), tmp_path)
    consumer._jobs_session_maker = session_factory
    job_id = await _insert_job(session_factory, kind="cron_user")
    msg = CronUserQueueMsg(
        job_id=job_id,
        owner_telegram_id=1,
        chat_id=99,
        command="echo hi",
        correlation_id="c1",
        scheduled_at_utc=datetime.now(UTC),
    )

    await consumer._execute_one(msg)

    # _execute_cron_user's _deliver() runs text through ChainSplitter, which
    # always appends a "(i/M)" footer (tg/output.py's _with_footer) — even for
    # a single short chunk. Unchanged pre-existing behavior (this test only
    # regression-checks that the cron_user path still dispatches correctly).
    bot.send_message.assert_awaited_once_with(99, "done\n(1/1)")
