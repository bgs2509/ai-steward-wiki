from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.scheduler.firing import (
    FiringNotInitialisedError,
    create_reminder_job,
    fire_job,
    set_firing_context,
)
from ai_steward_wiki.scheduler.queue import Lane
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import ReminderPayload, parse_job_payload

WHEN = datetime(2026, 5, 13, 3, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory(tmp_path: Any):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_firing_ctx():
    firing._ctx = None
    yield
    firing._ctx = None


class _FakeScheduler:
    def __init__(self, session_factory) -> None:
        self.calls: list[dict[str, Any]] = []
        self._sf = session_factory

    def add_job(self, func, *, trigger, args, id, misfire_grace_time, **kw) -> None:
        self.calls.append(
            {
                "func": func,
                "trigger": trigger,
                "args": args,
                "id": id,
                "misfire": misfire_grace_time,
            }
        )


class _FakeSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail = fail

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> object:
        if self._fail:
            raise RuntimeError("chat blocked")
        self.sent.append((chat_id, text))
        return object()


async def _insert_job(
    factory,
    *,
    status: str = "pending",
    user_state: str = "pending",
    message: str = "позвонить врачу",
) -> int:
    async with factory() as s:
        job = Job(
            owner_telegram_id=42,
            chat_id=42,
            kind="reminder_job",
            status=status,
            user_state=user_state,
            priority=int(Lane.USER_WRITE),
            scheduled_at_utc=WHEN.replace(tzinfo=None),
            payload=ReminderPayload(message=message).model_dump(mode="json"),
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(job)
        await s.commit()
        return job.id


# --- create_reminder_job ---------------------------------------------------


async def test_create_reminder_job_writes_row_and_schedules(session_factory) -> None:
    sched = _FakeScheduler(session_factory)
    async with session_factory() as s:
        job_id = await create_reminder_job(
            s,
            sched,
            owner_telegram_id=42,
            chat_id=42,
            when_utc=WHEN,
            message="позвонить врачу",
            lead_time_min=0,
        )
    assert isinstance(job_id, int)
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.kind == "reminder_job"
        assert row.status == "pending"
        assert row.priority == int(Lane.USER_WRITE)
        assert row.scheduled_at_utc == WHEN.replace(tzinfo=None)
        assert row.created_at_utc is not None
        payload = parse_job_payload(row.payload)
        assert isinstance(payload, ReminderPayload)
        assert payload.message == "позвонить врачу"
    assert len(sched.calls) == 1
    call = sched.calls[0]
    assert call["func"] is fire_job
    assert call["args"] == [job_id]
    assert call["id"] == f"reminder:{job_id}"
    assert call["misfire"] is None
    # trigger is a DateTrigger at WHEN
    assert getattr(call["trigger"], "run_date", None) is not None
    assert call["trigger"].run_date.astimezone(UTC) == WHEN


async def test_create_commits_row_before_add_job(session_factory, tmp_path: Any) -> None:
    # The Job row must be committed BEFORE scheduler.add_job runs: the fake
    # scheduler opens a fresh sqlite3 connection and asserts the row is visible.
    import sqlite3

    db_path = str(tmp_path / "jobs.db")
    seen: list[int] = []

    class _Probe:
        def add_job(self, func, **kw: Any) -> None:
            with sqlite3.connect(db_path) as conn:
                seen.append(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    async with session_factory() as s:
        await create_reminder_job(
            s, _Probe(), owner_telegram_id=1, chat_id=1, when_utc=WHEN, message="x"
        )
    assert seen == [1]  # row was already committed when add_job fired


# --- fire_job --------------------------------------------------------------


async def test_fire_job_delivers_and_marks_done(session_factory) -> None:
    sender = _FakeSender()
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    job_id = await _insert_job(session_factory)
    await fire_job(job_id)
    assert sender.sent == [(42, "\U0001f514 Напоминание: позвонить врачу")]
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.status == "done"
        assert row.started_at_utc is not None
        assert row.finished_at_utc is not None


async def test_fire_job_skips_non_pending(session_factory) -> None:
    sender = _FakeSender()
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    job_id = await _insert_job(session_factory, status="cancelled")
    await fire_job(job_id)
    assert sender.sent == []
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.status == "cancelled"


@pytest.mark.parametrize("user_state", ["done", "skipped"])
async def test_fire_job_suppressed_when_user_resolved(session_factory, user_state: str) -> None:
    # aisw-z0s: a still-pending reminder whose card the user already resolved
    # (user_state terminal) must NOT be delivered when its DateTrigger fires.
    sender = _FakeSender()
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    job_id = await _insert_job(session_factory, status="pending", user_state=user_state)
    await fire_job(job_id)
    assert sender.sent == []
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        # status untouched (two-lifecycle design preserved); fire was suppressed.
        assert row.status == "pending"
        assert row.user_state == user_state
        assert row.started_at_utc is None


async def test_fire_job_missing_row_is_noop(session_factory) -> None:
    sender = _FakeSender()
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    await fire_job(999_999)
    assert sender.sent == []


async def test_fire_job_send_failure_marks_failed(session_factory) -> None:
    sender = _FakeSender(fail=True)
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    job_id = await _insert_job(session_factory)
    await fire_job(job_id)  # must not raise
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.status == "failed"
        assert row.last_error is not None
        assert "chat blocked" in row.last_error or "RuntimeError" in row.last_error


async def test_fire_job_without_context_raises(session_factory) -> None:
    job_id = await _insert_job(session_factory)
    with pytest.raises(FiringNotInitialisedError):
        await fire_job(job_id)


# --- digest_job (aisw-oqq) -------------------------------------------------


from pathlib import Path  # noqa: E402

from alembic import command as _alembic_command  # noqa: E402
from alembic.config import Config as _AlembicConfig  # noqa: E402
from sqlalchemy import select as _sa_select  # noqa: E402

from ai_steward_wiki.classifier.recurrence import Recurrence  # noqa: E402
from ai_steward_wiki.scheduler.firing import (  # noqa: E402
    DigestNotInitialisedError,
    create_digest_job,
    disable_digest_jobs,
    fire_digest_job,
    reschedule_digest_jobs,
    set_digest_context,
)
from ai_steward_wiki.storage.jobs.models import JobDLQ  # noqa: E402
from ai_steward_wiki.storage.jobs.payloads import DigestPayload  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def audit_session_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("AISW_AUDIT_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = _AlembicConfig(str(_REPO_ROOT / "alembic" / "audit" / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic" / "audit"))
    _alembic_command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_digest_ctx():
    import ai_steward_wiki.scheduler.firing as _f

    _f._digest_ctx = None
    yield
    _f._digest_ctx = None


def _rec() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")


class _FakeCronScheduler:
    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []
        self.removed: list[str] = []
        self.rescheduled: list[dict[str, Any]] = []

    def add_job(self, func, *, trigger, args, id, replace_existing=False, **kw) -> None:
        self.added.append(
            {
                "func": func,
                "trigger": trigger,
                "args": args,
                "id": id,
                "replace_existing": replace_existing,
            }
        )

    def remove_job(self, job_id: str) -> None:
        self.removed.append(job_id)

    def reschedule_job(self, job_id: str, *, trigger) -> None:
        self.rescheduled.append({"id": job_id, "trigger": trigger})


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _DigestSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self.documents: list[tuple[int, str]] = []
        self._fail = fail

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> object:
        if self._fail:
            raise RuntimeError("chat blocked")
        self.sent.append((chat_id, text))
        return object()

    async def send_document(
        self, chat_id: int, *, path: object, caption: str = "", **kw: Any
    ) -> object:
        if self._fail:
            raise RuntimeError("chat blocked")
        self.documents.append((chat_id, str(path)))
        return object()


async def _resolve_two(owner_id: int):
    # Fake paths — only for tests that return before deliver_output touches them.
    return [("medical", Path("/w/u/Medical-WIKI")), ("finance", Path("/w/u/Finance-WIKI"))]


async def _resolve_none(owner_id: int):
    return []


@pytest.fixture
def wiki_dirs(tmp_path):
    medical = tmp_path / "Medical-WIKI"
    finance = tmp_path / "Finance-WIKI"
    medical.mkdir()
    finance.mkdir()
    return medical, finance


def _make_resolve_two(medical: Path, finance: Path):
    async def _resolve(owner_id: int):
        return [("medical", medical), ("finance", finance)]

    return _resolve


class _OkRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        wiki_id,
        wiki_path,
        extra_add_dirs,
        planner_context,
        correlation_id,
        section=None,
    ):
        self.calls.append(
            {
                "wiki_id": wiki_id,
                "wiki_path": wiki_path,
                "extra_add_dirs": extra_add_dirs,
                "planner_context": planner_context,
                "section": section,
            }
        )
        return "TL;DR: всё спокойно.\n📅 Сегодня: —"


class _FailRunner:
    async def __call__(self, **kw):
        from ai_steward_wiki.wiki.runner import WikiRunnerError

        raise WikiRunnerError("boom")


async def test_create_digest_job_writes_row_and_cron(session_factory) -> None:
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s, sched, owner_telegram_id=7, chat_id=7, recurrence=_rec(), window_hours=24
        )
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.kind == "digest_job"
        assert row.status == "scheduled"
        assert row.priority == int(Lane.DIGEST)
        parsed = parse_job_payload(row.payload)
        assert isinstance(parsed, DigestPayload)
        assert parsed.recurrence == _rec()
        assert parsed.wiki_scope == "all"
    assert len(sched.added) == 1
    call = sched.added[0]
    assert call["func"] is fire_digest_job
    assert call["args"] == [job_id]
    assert call["id"] == f"digest:{job_id}"
    assert call["replace_existing"] is True
    # CronTrigger encodes hour=9, minute=0
    assert "hour='9'" in str(call["trigger"]) or "hour=9" in repr(call["trigger"])


async def test_disable_digest_jobs_disables_and_removes_trigger(session_factory) -> None:
    # #2 aisw-578 — «выключи сводку» disables the job and drops its CronTrigger.
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s, sched, owner_telegram_id=7, chat_id=7, recurrence=_rec()
        )
    async with session_factory() as s:
        count = await disable_digest_jobs(s, sched, owner_telegram_id=7)
    assert count == 1
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.status == "disabled"
    assert f"digest:{job_id}" in sched.removed


async def test_disable_digest_jobs_no_jobs_returns_zero(session_factory) -> None:
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        count = await disable_digest_jobs(s, sched, owner_telegram_id=999)
    assert count == 0
    assert sched.removed == []


async def test_reschedule_digest_jobs_updates_time_and_trigger(session_factory) -> None:
    # #2 aisw-578 — «переноси сводку на 7:30» keeps kind/tz, only the time moves.
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s, sched, owner_telegram_id=7, chat_id=7, recurrence=_rec()
        )
    async with session_factory() as s:
        count = await reschedule_digest_jobs(s, sched, owner_telegram_id=7, time_hhmm="07:30")
    assert count == 1
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        parsed = parse_job_payload(row.payload)
        assert isinstance(parsed, DigestPayload)
        assert parsed.recurrence.time_hhmm == "07:30"
        assert parsed.recurrence.kind == "daily"
        assert parsed.recurrence.tz == "Europe/Moscow"
    assert sched.rescheduled
    assert sched.rescheduled[0]["id"] == f"digest:{job_id}"


async def test_reschedule_digest_jobs_no_jobs_returns_zero(session_factory) -> None:
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        count = await reschedule_digest_jobs(s, sched, owner_telegram_id=999, time_hhmm="07:30")
    assert count == 0
    assert sched.rescheduled == []


async def test_create_digest_job_named_subset_scope(session_factory) -> None:
    # aisw-269 — create_digest_job accepts wiki_scope: str | list[str].
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=_rec(),
            wiki_scope=["Health", "Money"],
        )
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        parsed = parse_job_payload(row.payload)
        assert isinstance(parsed, DigestPayload)
        assert parsed.wiki_scope == ["Health", "Money"]
    assert len(sched.added) == 1


async def test_list_owner_digest_job_ids(
    session_factory, audit_session_maker, wiki_dirs, sessions_factory
) -> None:
    # aisw-269 — only the owner's enabled (status=='scheduled') digest_job ids.
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        a = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
        b = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="20:00", tz="UTC"),
        )
        other = await create_digest_job(
            s,
            sched,
            owner_telegram_id=99,
            chat_id=99,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
        c = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="06:00", tz="UTC"),
        )
        row_c = await s.get(Job, c)
        assert row_c is not None
        row_c.status = "disabled"
        await s.commit()
    from ai_steward_wiki.scheduler.firing import list_owner_digest_job_ids

    set_digest_context(
        scheduler=sched,
        runner=_OkRunner(),
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    ids = await list_owner_digest_job_ids(7)
    assert set(ids) == {a, b}
    assert other not in ids
    assert c not in ids


async def test_run_section_expand(
    session_factory, audit_session_maker, wiki_dirs, sessions_factory
) -> None:
    # aisw-269 — re-run Claude scoped to one section over the owner's WIKI set.
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    runner = _OkRunner()
    from ai_steward_wiki.scheduler.firing import run_section_expand

    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    out = await run_section_expand(7, "trackers")
    assert isinstance(out, str)
    assert runner.calls[0]["wiki_id"] == "medical"
    assert runner.calls[0]["extra_add_dirs"] == [finance]
    assert runner.calls[0]["section"] == "trackers"

    async def _resolve_none(owner_id: int):
        return []

    set_digest_context(
        scheduler=sched,
        runner=_OkRunner(),
        resolve_owner_wikis=_resolve_none,
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    assert await run_section_expand(7, "today") is None


async def test_fire_digest_job_runs_and_delivers(
    session_factory, audit_session_maker, wiki_dirs, sessions_factory
) -> None:
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
    runner = _OkRunner()
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    assert len(sender.sent) == 1
    assert "TL;DR" in sender.sent[0][1]
    # primary WIKI is the first; the rest are extra_add_dirs
    assert runner.calls[0]["wiki_id"] == "medical"
    assert runner.calls[0]["extra_add_dirs"] == [finance]
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.status == "scheduled"
        assert row.retry_count == 0
        assert row.finished_at_utc is not None


async def test_fire_digest_job_scope_filter_keeps_named_subset(
    session_factory, audit_session_maker, wiki_dirs, sessions_factory
) -> None:
    # aisw-269 — a digest job scoped to ['medical'] runs only that WIKI.
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
            wiki_scope=["medical"],
        )
    runner = _OkRunner()
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    assert runner.calls[0]["wiki_id"] == "medical"
    assert runner.calls[0]["extra_add_dirs"] == []
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.status == "scheduled"
        assert row.retry_count == 0


async def test_fire_digest_job_scope_all_vanished_notice_no_strike(
    session_factory, audit_session_maker, wiki_dirs, sessions_factory
) -> None:
    # aisw-269 — scoped to a WIKI that no longer exists → ru notice, no run, no strike.
    medical, _finance = wiki_dirs
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
            wiki_scope=["gone"],
        )
    runner = _OkRunner()
    sender = _DigestSender()

    async def _resolve_one(owner_id: int):
        return [("medical", medical)]

    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_resolve_one,
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    assert runner.calls == []  # never ran Claude
    assert len(sender.sent) == 1  # the ru "vanished" notice
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.status == "scheduled"  # no strike
        assert row.retry_count == 0
        assert row.finished_at_utc is not None
    assert sched.removed == []  # no remove_job


async def test_fire_digest_job_delivers_via_deliver_output(
    session_factory, audit_session_maker, wiki_dirs, sessions_factory
) -> None:
    from ai_steward_wiki.storage.audit.models import RunOutput

    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched,
        runner=_OkRunner(),
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    runs_root = medical / "data" / "runs"
    assert runs_root.is_dir()
    md_files = list(runs_root.rglob("*.md"))
    assert len(md_files) == 1
    assert "TL;DR" in md_files[0].read_text(encoding="utf-8")
    async with audit_session_maker() as s:
        rows = (await s.execute(_sa_select(RunOutput))).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "digest"
    assert rows[0].job_id == job_id
    assert rows[0].owner_telegram_id == 7
    assert len(sender.sent) == 1


async def test_fire_digest_job_deliver_failure_strikes(
    session_factory, audit_session_maker, wiki_dirs, sessions_factory
) -> None:
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
    set_digest_context(
        scheduler=sched,
        runner=_OkRunner(),
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(fail=True),
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.retry_count == 1
        assert row.status == "scheduled"


async def test_build_planner_context_lists_in_window_jobs(session_factory) -> None:
    from datetime import timedelta

    from ai_steward_wiki.scheduler.firing import _build_planner_context

    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as s:
        s.add(
            Job(
                owner_telegram_id=7,
                chat_id=7,
                kind="reminder_job",
                status="scheduled",
                priority=int(Lane.DIGEST),
                scheduled_at_utc=now + timedelta(hours=2),
                payload=ReminderPayload(message="приём ферретаб").model_dump(mode="json"),
                created_at_utc=now,
            )
        )
        s.add(
            Job(
                owner_telegram_id=7,
                chat_id=7,
                kind="reminder_job",
                status="scheduled",
                priority=int(Lane.DIGEST),
                scheduled_at_utc=now + timedelta(hours=48),
                payload=ReminderPayload(message="через два дня").model_dump(mode="json"),
                created_at_utc=now,
            )
        )
        s.add(
            Job(
                owner_telegram_id=99,
                chat_id=99,
                kind="reminder_job",
                status="scheduled",
                priority=int(Lane.DIGEST),
                scheduled_at_utc=now + timedelta(hours=1),
                payload=ReminderPayload(message="чужое").model_dump(mode="json"),
                created_at_utc=now,
            )
        )
        await s.commit()
        ctx = await _build_planner_context(
            s, owner_telegram_id=7, window_hours=24, now_utc=now, tz="UTC"
        )
    assert "приём ферретаб" in ctx
    assert "через два дня" not in ctx
    assert "чужое" not in ctx
    assert "ближайшие 24 ч" in ctx


async def test_build_planner_context_empty(session_factory) -> None:
    from ai_steward_wiki.scheduler.firing import _build_planner_context

    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as s:
        ctx = await _build_planner_context(
            s, owner_telegram_id=7, window_hours=24, now_utc=now, tz="UTC"
        )
    assert ctx == "На ближайшие 24 ч ничего не запланировано."


async def test_fire_digest_job_no_wiki_set(
    session_factory, audit_session_maker, sessions_factory
) -> None:
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched,
        runner=_OkRunner(),
        resolve_owner_wikis=_resolve_none,
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    assert len(sender.sent) == 1
    assert "WIKI" in sender.sent[0][1]
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.status == "scheduled"
        assert row.retry_count == 0


async def test_fire_digest_job_third_failure_disables(
    session_factory, audit_session_maker, sessions_factory
) -> None:
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
    set_digest_context(
        scheduler=sched,
        runner=_FailRunner(),
        resolve_owner_wikis=_resolve_two,
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    await fire_digest_job(job_id)
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.status == "scheduled"
        assert row.retry_count == 2
    await fire_digest_job(job_id)
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.status == "disabled"
        assert row.retry_count == 3
        dlq = (await s.execute(_sa_select(JobDLQ).where(JobDLQ.job_id == job_id))).scalars().all()
        assert len(dlq) == 1
    assert f"digest:{job_id}" in sched.removed


async def test_fire_digest_job_bad_payload_disables(
    session_factory, audit_session_maker, sessions_factory
) -> None:
    async with session_factory() as s:
        job = Job(
            owner_telegram_id=7,
            chat_id=7,
            kind="digest_job",
            status="scheduled",
            priority=int(Lane.DIGEST),
            scheduled_at_utc=None,
            payload={"kind": "digest", "bogus": 1},
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(job)
        await s.commit()
        job_id = job.id
    set_digest_context(
        scheduler=_FakeCronScheduler(),
        runner=_OkRunner(),
        resolve_owner_wikis=_resolve_none,
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.status == "disabled"
        dlq = (await s.execute(_sa_select(JobDLQ).where(JobDLQ.job_id == job_id))).scalars().all()
        assert len(dlq) == 1


async def test_fire_digest_job_skips_non_scheduled(
    session_factory, audit_session_maker, sessions_factory
) -> None:
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
        row = await s.get(Job, job_id)
        row.status = "disabled"
        await s.commit()
    runner = _OkRunner()
    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_resolve_none,
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    assert runner.calls == []


async def test_fire_digest_job_without_context_raises() -> None:
    with pytest.raises(DigestNotInitialisedError):
        await fire_digest_job(123)


# --- digest section toggles (aisw-pv8) -------------------------------------


@pytest.fixture
async def sessions_factory(tmp_path):
    from ai_steward_wiki.storage.sessions.engine import Base as SBase

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SBase.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_sessions_user(sessions_maker, telegram_id: int) -> None:
    from ai_steward_wiki.storage.sessions.models import User

    async with sessions_maker() as s:
        s.add(
            User(
                telegram_id=telegram_id,
                role="user",
                display_name="t",
                tz="Europe/Moscow",
                enabled=True,
                created_at_utc=datetime.now(UTC).replace(tzinfo=None),
                updated_at_utc=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await s.commit()


async def test_owner_digest_prefs_accessors(
    session_factory, audit_session_maker, sessions_factory
) -> None:
    await _seed_sessions_user(sessions_factory, 70)
    set_digest_context(
        scheduler=_FakeCronScheduler(),
        runner=_OkRunner(),
        resolve_owner_wikis=_resolve_none,
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    assert (await firing.get_owner_digest_prefs(70)).disabled_keys == ()
    after = await firing.set_owner_digest_section(70, section="trackers", enabled=False)
    assert after.disabled_keys == ("trackers",)
    assert (await firing.get_owner_digest_prefs(70)).disabled_keys == ("trackers",)


async def test_owner_digest_prefs_accessors_without_context_raise() -> None:
    with pytest.raises(DigestNotInitialisedError):
        await firing.get_owner_digest_prefs(70)
    with pytest.raises(DigestNotInitialisedError):
        await firing.set_owner_digest_section(70, section="trackers", enabled=False)


# --- fire_digest_job honours user_digest_prefs (aisw-pv8) ------------------


async def _make_digest_job(session_factory, sched) -> int:
    async with session_factory() as s:
        return await create_digest_job(
            s,
            sched,
            owner_telegram_id=7,
            chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )


async def test_digest_no_prefs_planner_context_has_no_directive(
    session_factory, audit_session_maker, sessions_factory, wiki_dirs
) -> None:
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    job_id = await _make_digest_job(session_factory, sched)
    runner = _OkRunner()
    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    assert "Не включай разделы" not in runner.calls[0]["planner_context"]


async def test_digest_trackers_disabled_appends_directive(
    session_factory, audit_session_maker, sessions_factory, wiki_dirs
) -> None:
    import structlog

    from ai_steward_wiki.storage.sessions.digest_prefs import set_digest_section

    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    job_id = await _make_digest_job(session_factory, sched)
    await _seed_sessions_user(sessions_factory, 7)
    await set_digest_section(sessions_factory, 7, section="trackers", enabled=False)
    runner = _OkRunner()
    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    with structlog.testing.capture_logs() as logs:
        await fire_digest_job(job_id)
    assert runner.calls[0]["planner_context"].rstrip().endswith("Не включай разделы: 📈 Трекеры.")
    assert any(
        e["event"] == "scheduler.digest.sections_filtered" and e["disabled"] == ["trackers"]
        for e in logs
    )


async def test_digest_both_disabled_lists_both(
    session_factory, audit_session_maker, sessions_factory, wiki_dirs
) -> None:
    from ai_steward_wiki.storage.sessions.digest_prefs import set_digest_section

    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    job_id = await _make_digest_job(session_factory, sched)
    await _seed_sessions_user(sessions_factory, 7)
    await set_digest_section(sessions_factory, 7, section="trackers", enabled=False)
    await set_digest_section(sessions_factory, 7, section="wiki", enabled=False)
    runner = _OkRunner()
    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=_DigestSender(),
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    assert (
        runner.calls[0]["planner_context"]
        .rstrip()
        .endswith("Не включай разделы: 📈 Трекеры, 📝 Обновления WIKI.")
    )


async def test_digest_prefs_read_failure_degrades_to_all_on(
    session_factory, audit_session_maker, sessions_factory, wiki_dirs, monkeypatch
) -> None:
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    job_id = await _make_digest_job(session_factory, sched)
    runner = _OkRunner()
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )

    async def _raise(*_a, **_k):
        raise RuntimeError("prefs db down")

    monkeypatch.setattr(firing, "get_digest_prefs", _raise)
    await fire_digest_job(job_id)
    assert "Не включай разделы" not in runner.calls[0]["planner_context"]
    assert len(sender.sent) == 1  # digest still delivered


# --- aisw-163 P5: cards emission wiring -----------------------------------


async def _set_cards_pref(sessions_maker, telegram_id: int, *, enabled: bool) -> None:
    from ai_steward_wiki.storage.sessions.digest_prefs import set_cards_enabled

    await _seed_sessions_user(sessions_maker, telegram_id)
    await set_cards_enabled(sessions_maker, telegram_id, enabled=enabled)


async def _seed_pending_reminder(session_factory, *, owner: int, when_utc) -> int:
    async with session_factory() as s:
        j = Job(
            owner_telegram_id=owner,
            chat_id=owner,
            kind="reminder_job",
            status="scheduled",
            priority=int(Lane.USER_WRITE),
            scheduled_at_utc=when_utc,
            payload=ReminderPayload(message="принять аспирин", category="medication").model_dump(),
            user_state="pending",
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(j)
        await s.commit()
        return j.id


async def test_digest_emits_cards_when_prefs_enabled(
    session_factory, audit_session_maker, sessions_factory, wiki_dirs
) -> None:
    # Default cards_enabled=True ⇒ emit_reminder_cards is called after delivery.
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    job_id = await _make_digest_job(session_factory, sched)
    # one pending reminder in the ±2h window (UTC now-ish)
    await _seed_pending_reminder(
        session_factory, owner=7, when_utc=datetime.now(UTC).replace(tzinfo=None)
    )
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched,
        runner=_OkRunner(),
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    # digest body + 1 card
    assert len(sender.sent) >= 2
    # the card text mentions the reminder message
    assert any("аспирин" in text for _, text in sender.sent)


async def test_digest_skips_cards_when_prefs_disabled(
    session_factory, audit_session_maker, sessions_factory, wiki_dirs
) -> None:
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    job_id = await _make_digest_job(session_factory, sched)
    await _seed_pending_reminder(
        session_factory, owner=7, when_utc=datetime.now(UTC).replace(tzinfo=None)
    )
    await _set_cards_pref(sessions_factory, 7, enabled=False)
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched,
        runner=_OkRunner(),
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )
    await fire_digest_job(job_id)
    # only the digest body — no cards
    assert len(sender.sent) == 1
    assert not any("аспирин" in text for _, text in sender.sent)


async def test_digest_cards_failure_does_not_strike_digest(
    session_factory, audit_session_maker, sessions_factory, wiki_dirs, monkeypatch
) -> None:
    medical, finance = wiki_dirs
    sched = _FakeCronScheduler()
    job_id = await _make_digest_job(session_factory, sched)
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched,
        runner=_OkRunner(),
        resolve_owner_wikis=_make_resolve_two(medical, finance),
        jobs_session_maker=session_factory,
        audit_session_maker=audit_session_maker,
        sender=sender,
        sessions_session_maker=sessions_factory,
    )

    async def _raise(**_kw):
        raise RuntimeError("cards backend down")

    monkeypatch.setattr(firing, "emit_reminder_cards", _raise)
    await fire_digest_job(job_id)
    # digest itself still delivered, row stays scheduled, retry_count 0.
    assert len(sender.sent) == 1
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.status == "scheduled"
        assert row.retry_count == 0
        assert row.finished_at_utc is not None
