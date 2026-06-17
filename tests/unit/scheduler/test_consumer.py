"""M-SCHEDULER-CONSUMER tests (aisw-02v).

CronConsumer drains a real PriorityJobQueue, spawns a stub subprocess via the
Spawner protocol seam, delivers to a fake aiogram.Bot, and updates jobs.Job
status. Tests do not touch systemd-run, asyncio.create_subprocess_exec, or
aiogram — every external dependency is faked.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler.consumer import CronConsumer
from ai_steward_wiki.scheduler.queue import Lane, PriorityJobQueue
from ai_steward_wiki.scheduler.queue_payloads import CronUserQueueMsg
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import CronUserPayload


@pytest.fixture
async def session_factory(tmp_path: Any):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class _FakeBot:
    def __init__(self, *, send_raises: BaseException | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self._send_raises = send_raises

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> None:
        if self._send_raises is not None:
            raise self._send_raises
        self.sent.append((chat_id, text))


class _FakeProc:
    """asyncio.subprocess.Process subset that the consumer awaits."""

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self._terminated = False
        self._killed = False
        self._exit = asyncio.Event()
        if not hang:
            self._exit.set()

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            # Block until terminate() or kill() is called.
            await self._exit.wait()
        return self._stdout, self._stderr

    def terminate(self) -> None:
        self._terminated = True
        self._exit.set()

    def kill(self) -> None:
        self._killed = True
        self._exit.set()

    async def wait(self) -> int:
        await self._exit.wait()
        return self.returncode


class _StubSpawner:
    """Records the last argv and returns a pre-built _FakeProc."""

    def __init__(self, proc: _FakeProc) -> None:
        self.proc = proc
        self.argv: list[str] | None = None
        self.env: dict[str, str] | None = None
        self.cwd: Path | None = None

    async def spawn(
        self,
        argv,
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> _FakeProc:
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        return self.proc


async def _insert_queued_job(factory) -> int:
    async with factory() as s:
        rec = Recurrence(kind="daily", time_hhmm="09:00", tz="UTC")
        payload = CronUserPayload(recurrence=rec, command="hi")
        job = Job(
            owner_telegram_id=100,
            chat_id=100,
            kind="cron_user",
            status="queued",
            priority=int(Lane.CRON_WRITE),
            payload=payload.model_dump(mode="json"),
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(job)
        await s.commit()
        return job.id


def _msg(job_id: int, command: str = "hi") -> CronUserQueueMsg:
    return CronUserQueueMsg(
        job_id=job_id,
        owner_telegram_id=100,
        chat_id=100,
        command=command,
        correlation_id="cid-abc",
        scheduled_at_utc=datetime.now(UTC),
    )


def _build_consumer(*, session_factory, bot, spawner) -> CronConsumer:
    return CronConsumer(
        queue=PriorityJobQueue(),
        bot=bot,
        claude_binary="/usr/bin/echo",  # arbitrary, never executed thanks to stub spawner
        claude_config_dir=Path("/var/lib/ai-steward-wiki/claude-code"),
        prompt_path=Path(__file__).parent / "_fixtures_prompt.md",
        jobs_session_maker=session_factory,
        timeout_s=600,
        slice_name="aisw-cli.slice",
        spawner=spawner,
    )


@pytest.fixture
def fake_prompt_file(tmp_path: Path):
    p = tmp_path / "prompt.md"
    p.write_text("system prompt body", encoding="utf-8")
    return p


# --- happy path ------------------------------------------------------------


async def test_execute_one_happy_path(session_factory, fake_prompt_file) -> None:
    job_id = await _insert_queued_job(session_factory)
    bot = _FakeBot()
    spawner = _StubSpawner(_FakeProc(stdout=b"hello\n", returncode=0))
    consumer = CronConsumer(
        queue=PriorityJobQueue(),
        bot=bot,
        claude_binary="/usr/bin/echo",
        claude_config_dir=Path("/var/lib/ai-steward-wiki/claude-code"),
        prompt_path=fake_prompt_file,
        jobs_session_maker=session_factory,
        timeout_s=600,
        slice_name="aisw-cli.slice",
        spawner=spawner,
    )
    msg = _msg(job_id, command="run me")
    await consumer._execute_one(msg)

    # Bot was called with stdout.
    assert len(bot.sent) == 1
    chat_id, text = bot.sent[0]
    assert chat_id == 100
    assert "hello" in text

    # argv contains the systemd-run scope + claude binary + command.
    assert spawner.argv is not None
    argv = spawner.argv
    assert argv[0] == "systemd-run"
    assert "--scope" in argv
    assert "--collect" in argv
    assert "--slice=aisw-cli.slice" in argv
    assert f"--unit=cli-{job_id}" in argv
    assert "--" in argv
    # The user command appears as the last argv element.
    assert argv[-1] == "run me"

    # Status mutated to 'finished'.
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "finished"
    assert row.finished_at_utc is not None


# --- timeout ---------------------------------------------------------------


async def test_execute_one_timeout(session_factory, fake_prompt_file) -> None:
    job_id = await _insert_queued_job(session_factory)
    bot = _FakeBot()
    # Hanging proc — wait_for will TimeoutError after timeout_s=0.05.
    spawner = _StubSpawner(_FakeProc(hang=True))
    consumer = CronConsumer(
        queue=PriorityJobQueue(),
        bot=bot,
        claude_binary="/usr/bin/echo",
        claude_config_dir=Path("/var/lib/ai-steward-wiki/claude-code"),
        prompt_path=fake_prompt_file,
        jobs_session_maker=session_factory,
        timeout_s=0.05,
        slice_name="aisw-cli.slice",
        spawner=spawner,
    )
    await consumer._execute_one(_msg(job_id))
    # ru timeout message.
    assert len(bot.sent) == 1
    _, text = bot.sent[0]
    assert text.startswith("❌")
    assert "айм" in text  # «Тайм-аут»
    # Proc was terminated (kill_with_sequence).
    assert spawner.proc._terminated  # type: ignore[union-attr]
    # Status='failed'.
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "failed"
    assert row.last_error is not None
    assert "timeout" in row.last_error.lower()


# --- non-zero exit ---------------------------------------------------------


async def test_execute_one_non_zero_exit(session_factory, fake_prompt_file) -> None:
    job_id = await _insert_queued_job(session_factory)
    bot = _FakeBot()
    spawner = _StubSpawner(_FakeProc(stdout=b"", stderr=b"boom\n", returncode=1))
    consumer = CronConsumer(
        queue=PriorityJobQueue(),
        bot=bot,
        claude_binary="/usr/bin/echo",
        claude_config_dir=Path("/var/lib/ai-steward-wiki/claude-code"),
        prompt_path=fake_prompt_file,
        jobs_session_maker=session_factory,
        timeout_s=600,
        slice_name="aisw-cli.slice",
        spawner=spawner,
    )
    await consumer._execute_one(_msg(job_id))
    assert len(bot.sent) == 1
    _, text = bot.sent[0]
    assert "❌" in text
    assert "Ошибка" in text
    assert "1" in text  # exit code
    assert "boom" in text
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "failed"


# --- chunking --------------------------------------------------------------


async def test_execute_one_long_stdout_chunked(session_factory, fake_prompt_file) -> None:
    job_id = await _insert_queued_job(session_factory)
    bot = _FakeBot()
    big = ("A" * 100 + "\n\n") * 200  # ~20 200 chars → forces ChainSplitter chunking
    spawner = _StubSpawner(_FakeProc(stdout=big.encode(), returncode=0))
    consumer = CronConsumer(
        queue=PriorityJobQueue(),
        bot=bot,
        claude_binary="/usr/bin/echo",
        claude_config_dir=Path("/var/lib/ai-steward-wiki/claude-code"),
        prompt_path=fake_prompt_file,
        jobs_session_maker=session_factory,
        timeout_s=600,
        slice_name="aisw-cli.slice",
        spawner=spawner,
    )
    await consumer._execute_one(_msg(job_id))
    assert len(bot.sent) >= 2  # multiple chunks
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "finished"


# --- TG send failure -------------------------------------------------------


async def test_execute_one_telegram_error_does_not_propagate(
    session_factory, fake_prompt_file
) -> None:
    job_id = await _insert_queued_job(session_factory)
    bot = _FakeBot(send_raises=TelegramAPIError(method=None, message="blocked"))  # type: ignore[arg-type]
    spawner = _StubSpawner(_FakeProc(stdout=b"ok", returncode=0))
    consumer = CronConsumer(
        queue=PriorityJobQueue(),
        bot=bot,
        claude_binary="/usr/bin/echo",
        claude_config_dir=Path("/var/lib/ai-steward-wiki/claude-code"),
        prompt_path=fake_prompt_file,
        jobs_session_maker=session_factory,
        timeout_s=600,
        slice_name="aisw-cli.slice",
        spawner=spawner,
    )
    # Must not raise.
    await consumer._execute_one(_msg(job_id))
    # Status reflects delivery failure.
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "failed"
    assert row.last_error is not None


# --- run loop / cancellation -----------------------------------------------


async def test_run_loop_drains_and_cancels(session_factory, fake_prompt_file) -> None:
    job_id = await _insert_queued_job(session_factory)
    bot = _FakeBot()
    spawner = _StubSpawner(_FakeProc(stdout=b"done", returncode=0))
    queue = PriorityJobQueue()
    consumer = CronConsumer(
        queue=queue,
        bot=bot,
        claude_binary="/usr/bin/echo",
        claude_config_dir=Path("/var/lib/ai-steward-wiki/claude-code"),
        prompt_path=fake_prompt_file,
        jobs_session_maker=session_factory,
        timeout_s=600,
        slice_name="aisw-cli.slice",
        spawner=spawner,
    )
    await queue.put(Lane.CRON_WRITE, _msg(job_id))
    task = asyncio.create_task(consumer.run(), name="cron_consumer_test")
    # Wait until the item is delivered.
    for _ in range(50):
        if bot.sent:
            break
        await asyncio.sleep(0.01)
    assert bot.sent
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- argv flag-injection guard (aisw-0j4) ----------------------------------


def test_build_argv_separates_command_with_double_dash(session_factory, fake_prompt_file) -> None:
    """A literal '--' MUST precede msg.command so the Claude CLI can never parse
    a '-'-prefixed command as a flag (sandbox-bypass guard, aisw-0j4).
    """
    bot = _FakeBot()
    spawner = _StubSpawner(_FakeProc(stdout=b"X", returncode=0))
    consumer = _build_consumer(session_factory=session_factory, bot=bot, spawner=spawner)
    consumer._prompt_path = fake_prompt_file

    # A command that, without the guard, the Claude CLI would parse as a flag.
    danger = "--dangerously-skip-permissions"
    argv = consumer._build_argv(_msg(1, command=danger))

    # The command is present and last...
    assert argv[-1] == danger
    # ...and immediately preceded by a literal end-of-options separator.
    assert argv[-2] == "--"
    # That separator is the one guarding the command, not the systemd-run one:
    # the element directly before the command must be '--'.
    cmd_idx = argv.index(danger)
    assert argv[cmd_idx - 1] == "--"


def test_build_argv_normal_command_still_present(session_factory, fake_prompt_file) -> None:
    """The benign case keeps the command as the final positional argument."""
    bot = _FakeBot()
    spawner = _StubSpawner(_FakeProc(stdout=b"X", returncode=0))
    consumer = _build_consumer(session_factory=session_factory, bot=bot, spawner=spawner)
    consumer._prompt_path = fake_prompt_file

    argv = consumer._build_argv(_msg(1, command="summarise my week"))
    assert argv[-1] == "summarise my week"
    assert argv[-2] == "--"


# --- queue payload validation failure --------------------------------------


async def test_execute_one_ignores_non_message_items(session_factory, fake_prompt_file) -> None:
    """If something garbage ends up on the queue, the consumer logs + skips."""
    bot = _FakeBot()
    spawner = _StubSpawner(_FakeProc(stdout=b"X", returncode=0))
    consumer = _build_consumer(
        session_factory=session_factory,
        bot=bot,
        spawner=spawner,
    )
    # Replace prompt_path — _build_consumer points at a missing file; but for
    # this test we never reach the spawn.
    consumer._prompt_path = fake_prompt_file
    await consumer._execute_one("not-a-msg")  # type: ignore[arg-type]
    assert bot.sent == []
    assert spawner.argv is None
