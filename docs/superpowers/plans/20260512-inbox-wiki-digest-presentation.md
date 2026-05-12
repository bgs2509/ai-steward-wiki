# Inbox-WIKI Phase-D.b.2a — digest presentation core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the recurring digest's delivery through the existing `tg.output.deliver_output(kind="digest")` (D-024/D-025: `<b>`-section split + `(n/m)` + `send_document` fallthrough + `data/runs/` persist + `audit.run_outputs` row), feed the digest prompt a real `jobs.db` window query instead of a one-line stub, and rewrite `prompts/digest.md` to the D-024 HTML+TL;DR contract.

**Architecture:** `scheduler/firing.py` owns the change: `set_digest_context` gains `audit_session_maker`; `_digest_ctx` becomes a 6-tuple; a new module-private `_build_planner_context` queries `jobs.Job` for the owner's scheduled one-shot jobs in the window; `fire_digest_job` calls `deliver_output` instead of the truncated `send_message`. `__main__` passes `audit_session_maker=audit_maker`. No new module, no new dependency, no migration. FR-2 (section split / `(n/m)` / `send_document` fallthrough) is already implemented in `ChainSplitter`/`deliver_output` — covered here by a digest-flavoured test, not new code.

**Tech Stack:** Python 3.11, aiogram 3.x, APScheduler, SQLAlchemy async, Pydantic v2, structlog, pytest+pytest-asyncio. bd: `aisw-w3k`. Spec: D-024, D-025; ADR-007; design `docs/superpowers/specs/20260512-inbox-wiki-digest-presentation-design.md`.

**bd_id:** `aisw-w3k` (Phase-D.b.2a). Sibling `aisw-269` (Phase-D.b.2b) owns cards / `/expand` / `/digest_now` / toggles / named-subset WIKI — out of scope here.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/ai_steward_wiki/scheduler/firing.py` | digest firing bridge | Modify: imports (`timedelta`, `select`, `ZoneInfo`); add `_build_planner_context`; `set_digest_context` `+audit_session_maker`; `_digest_ctx` 6-tuple; `fire_digest_job` → `deliver_output`; drop `_DIGEST_TG_LIMIT`; header contract+map+change-summary; `VERSION` bump |
| `src/ai_steward_wiki/__main__.py` | runtime wiring | Modify: `firing.set_digest_context(..., audit_session_maker=audit_maker)`; change-summary bump |
| `prompts/digest.md` | Stage-1 digest prompt | Rewrite to D-024 contract; `semver: 0.1.0` |
| `tests/unit/scheduler/test_firing.py` | digest firing tests | Modify: add `audit_session_maker` fixture; `_resolve_two`/`_resolve_none` use `tmp_path` dirs; `_DigestSender` `+send_document`; all `set_digest_context(...)` calls `+audit_session_maker=`; new tests for `deliver_output` routing + planner context |
| `tests/unit/tg/test_output.py` | output-policy tests | Modify: add `test_deliver_digest_splits_at_b_headers` and `test_deliver_digest_large_to_document` |
| `docs/adr/ADR-024-digest-presentation.md` | ADR | Create (in Finish) |
| `docs/Spec-WIKI/decisions/D-024-digest-format.md` | spec decision | Modify: tick the "перенос в ADR" checkbox (in Finish) |
| `docs/knowledge-graph.xml`, `docs/verification-plan.xml`, `docs/development-plan.xml` | GRACE artifacts | Regenerate via `grace-refresh` (in Finish) |

---

## Task 1: `_build_planner_context` — real jobs.db window query

**Files:**
- Modify: `src/ai_steward_wiki/scheduler/firing.py` (imports near line 46; new helper after the `_digest_ctx` block / before `create_digest_job`)
- Test: `tests/unit/scheduler/test_firing.py`

- [ ] **Step 1: Add the `audit_session_maker` fixture and a planner-context test** — append to `tests/unit/scheduler/test_firing.py` (after the existing digest tests, before `test_fire_digest_job_without_context_raises` is fine — just keep it inside the file). First add the imports the new code needs at the top of the digest section (next to the other `# noqa: E402` imports around line 200):

```python
from alembic import command as _alembic_command  # noqa: E402
from alembic.config import Config as _AlembicConfig  # noqa: E402

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
```

Then add the test:

```python
async def test_build_planner_context_lists_in_window_jobs(session_factory) -> None:
    from datetime import timedelta

    from ai_steward_wiki.scheduler.firing import _build_planner_context

    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as s:
        # in-window reminder at now+2h, message "приём ферретаб"
        s.add(
            Job(
                owner_telegram_id=7, chat_id=7, kind="reminder_job", status="scheduled",
                priority=int(Lane.DIGEST), scheduled_at_utc=now + timedelta(hours=2),
                payload=ReminderPayload(message="приём ферретаб").model_dump(mode="json"),
                created_at_utc=now,
            )
        )
        # out-of-window reminder at now+48h
        s.add(
            Job(
                owner_telegram_id=7, chat_id=7, kind="reminder_job", status="scheduled",
                priority=int(Lane.DIGEST), scheduled_at_utc=now + timedelta(hours=48),
                payload=ReminderPayload(message="через два дня").model_dump(mode="json"),
                created_at_utc=now,
            )
        )
        # other owner — must not appear
        s.add(
            Job(
                owner_telegram_id=99, chat_id=99, kind="reminder_job", status="scheduled",
                priority=int(Lane.DIGEST), scheduled_at_utc=now + timedelta(hours=1),
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
    assert ctx == "На ближайшие 24 ч ничего не запланировано."  # noqa: RUF001
```

- [ ] **Step 2: Run the new tests — expect ImportError/AttributeError**

Run: `uv run pytest tests/unit/scheduler/test_firing.py -k build_planner_context -v`
Expected: FAIL — `cannot import name '_build_planner_context'`.

- [ ] **Step 3: Add imports + `_build_planner_context` to `firing.py`**

In the import block (after `from datetime import UTC, datetime`) change to:

```python
from datetime import UTC, datetime, timedelta
```

Add (with the other third-party imports, after `from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker`):

```python
from sqlalchemy import select
```

Add at the end of the import block (stdlib group, top — next to `import contextlib`):

```python
from zoneinfo import ZoneInfo
```

Then add the helper just above `# START_BLOCK_CREATE_DIGEST_JOB`:

```python
async def _build_planner_context(
    session: AsyncSession,
    *,
    owner_telegram_id: int,
    window_hours: int,
    now_utc: datetime,
    tz: str,
) -> str:
    """Build the ru planner block fed into prompts/digest.md.

    Lists the owner's one-shot scheduled jobs whose ``scheduled_at_utc`` falls
    inside the digest window, rendered «- HH:MM — <title>» in the owner's tz.
    Recurring jobs (``scheduled_at_utc IS NULL`` — cron-driven) are excluded.
    """
    horizon = now_utc + timedelta(hours=window_hours)
    rows = (
        (
            await session.execute(
                select(Job)
                .where(
                    Job.owner_telegram_id == owner_telegram_id,
                    Job.status == "scheduled",
                    Job.scheduled_at_utc.is_not(None),
                    Job.scheduled_at_utc <= horizon,
                )
                .order_by(Job.scheduled_at_utc)
            )
        )
        .scalars()
        .all()
    )
    zone = ZoneInfo(tz)
    lines: list[str] = []
    for job in rows:
        try:
            p = parse_job_payload(job.payload)
        except ValidationError:
            continue
        title = (
            getattr(p, "message", None) or getattr(p, "prompt_hint", None) or job.kind
        )
        assert job.scheduled_at_utc is not None  # narrowed by the WHERE clause
        local = job.scheduled_at_utc.replace(tzinfo=UTC).astimezone(zone)
        lines.append(f"- {local:%H:%M} — {title}")
    if not lines:
        return f"На ближайшие {window_hours} ч ничего не запланировано."  # noqa: RUF001
    return f"Запланировано на ближайшие {window_hours} ч:\n" + "\n".join(lines)
```

- [ ] **Step 4: Run the new tests — expect PASS**

Run: `uv run pytest tests/unit/scheduler/test_firing.py -k build_planner_context -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_steward_wiki/scheduler/firing.py tests/unit/scheduler/test_firing.py
git commit -m "feat(M-SCHEDULER-FIRING): _build_planner_context — real jobs.db window query for the digest prompt (aisw-w3k)"
```

---

## Task 2: route `fire_digest_job` through `deliver_output`; grow `set_digest_context`

**Files:**
- Modify: `src/ai_steward_wiki/scheduler/firing.py` (digest section: `set_digest_context`, `_digest_ctx`, `fire_digest_job`, drop `_DIGEST_TG_LIMIT`)
- Test: `tests/unit/scheduler/test_firing.py`

- [ ] **Step 1: Update the existing digest test helpers + add the `deliver_output`-routing test**

In `tests/unit/scheduler/test_firing.py`:

(a) Make the resolver fixtures use real tmp dirs (so `deliver_output`'s `_persist_to_disk` can `mkdir`). Replace `_resolve_two` / `_resolve_none`:

```python
@pytest.fixture
def _wiki_dirs(tmp_path):
    health = tmp_path / "Health-WIKI"
    finance = tmp_path / "Finance-WIKI"
    health.mkdir()
    finance.mkdir()
    return health, finance


def _make_resolve_two(health: Path, finance: Path):
    async def _resolve_two(owner_id: int):
        return [("health", health), ("finance", finance)]

    return _resolve_two


async def _resolve_none(owner_id: int):
    return []
```

(b) Give `_DigestSender` a `send_document` no-op:

```python
    async def send_document(self, chat_id: int, *, path: object, caption: str = "", **kw: Any) -> object:
        self.sent.append((chat_id, f"[document {caption}]"))
        return object()
```

(c) Every call `set_digest_context(scheduler=..., runner=..., resolve_owner_wikis=..., jobs_session_maker=session_factory, sender=...)` in this file gets a new kwarg `audit_session_maker=audit_session_maker` (the fixture); each affected test grows `audit_session_maker` in its signature. The tests that pass `resolve_owner_wikis=_resolve_two` now take the `_wiki_dirs` fixture and pass `resolve_owner_wikis=_make_resolve_two(*_wiki_dirs)`.

   For `test_fire_digest_job_runs_and_delivers` the assertion `runner.calls[0]["extra_add_dirs"] == [Path("/w/u/Finance-WIKI")]` becomes `runner.calls[0]["extra_add_dirs"] == [_wiki_dirs[1]]`, and `assert "TL;DR" in sender.sent[0][1]` still holds (short text → inline send).

(d) Add a focused test asserting `deliver_output` was invoked (persisted file + audit row exist):

```python
async def test_fire_digest_job_delivers_via_deliver_output(
    session_factory, audit_session_maker, _wiki_dirs
) -> None:
    from sqlalchemy import select as _sel

    from ai_steward_wiki.storage.audit.models import RunOutput

    health, finance = _wiki_dirs
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s, sched, owner_telegram_id=7, chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
    sender = _DigestSender()
    set_digest_context(
        scheduler=sched, runner=_OkRunner(), resolve_owner_wikis=_make_resolve_two(health, finance),
        jobs_session_maker=session_factory, audit_session_maker=audit_session_maker, sender=sender,
    )
    await fire_digest_job(job_id)
    # persisted under <primary>/data/runs/<date>/<run_id>.md
    runs_root = health / "data" / "runs"
    assert runs_root.is_dir()
    md_files = list(runs_root.rglob("*.md"))
    assert len(md_files) == 1
    assert "TL;DR" in md_files[0].read_text(encoding="utf-8")
    # audit row
    async with audit_session_maker() as s:
        rows = (await s.execute(_sel(RunOutput))).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "digest"
    assert rows[0].job_id == job_id
    assert rows[0].owner_telegram_id == 7
    # TG got exactly one inline message
    assert len(sender.sent) == 1


async def test_fire_digest_job_deliver_failure_strikes(
    session_factory, audit_session_maker, _wiki_dirs
) -> None:
    health, finance = _wiki_dirs
    sched = _FakeCronScheduler()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s, sched, owner_telegram_id=7, chat_id=7,
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz="UTC"),
        )
    set_digest_context(
        scheduler=sched, runner=_OkRunner(), resolve_owner_wikis=_make_resolve_two(health, finance),
        jobs_session_maker=session_factory, audit_session_maker=audit_session_maker,
        sender=_DigestSender(fail=True),
    )
    await fire_digest_job(job_id)
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row.retry_count == 1
        assert row.status == "scheduled"
```

- [ ] **Step 2: Run the digest tests — expect FAIL (set_digest_context has no `audit_session_maker`)**

Run: `uv run pytest tests/unit/scheduler/test_firing.py -k digest -v`
Expected: FAIL — `TypeError: set_digest_context() got an unexpected keyword argument 'audit_session_maker'`.

- [ ] **Step 3: Update `firing.py` — `set_digest_context`, `_digest_ctx`, `fire_digest_job`**

Add the import (top of the file, third-party group near `deliver_output`'s neighbours — actually `deliver_output` lives in `ai_steward_wiki.tg.output`):

```python
from uuid import uuid4
```

and in the runtime-import group:

```python
from ai_steward_wiki.tg.output import deliver_output
```

Replace the `_DIGEST_TG_LIMIT` constant line (delete it) — drop:

```python
_DIGEST_TG_LIMIT = 4000  # plain-send safety cap; full delivery polish (D-024/D-025) is aisw-w3k
```

Change the `_digest_ctx` type and `set_digest_context`:

```python
# tuple: (scheduler, runner, resolve_owner_wikis, jobs_session_maker, audit_session_maker, sender)
_digest_ctx: (
    tuple[
        AsyncIOScheduler,
        DigestRunner,
        Callable[[int], Awaitable[Sequence[tuple[str, Path]]]],
        async_sessionmaker[AsyncSession],
        async_sessionmaker[AsyncSession],
        TgSender,
    ]
    | None
) = None


def set_digest_context(
    *,
    scheduler: AsyncIOScheduler,
    runner: DigestRunner,
    resolve_owner_wikis: Callable[[int], Awaitable[Sequence[tuple[str, Path]]]],
    jobs_session_maker: async_sessionmaker[AsyncSession],
    audit_session_maker: async_sessionmaker[AsyncSession],
    sender: TgSender,
) -> None:
    """Install the digest firing registry. Call once at startup."""
    global _digest_ctx
    _digest_ctx = (
        scheduler,
        runner,
        resolve_owner_wikis,
        jobs_session_maker,
        audit_session_maker,
        sender,
    )
```

In `fire_digest_job`, change the unpack and the body:

```python
    scheduler, runner, resolve_owner_wikis, maker, audit_maker, sender = _digest_ctx
```

Replace `planner_context = f"Окно сводки: ближайшие {payload.window_hours} ч."` with:

```python
        planner_context = await _build_planner_context(
            session,
            owner_telegram_id=owner_id,
            window_hours=payload.window_hours,
            now_utc=_now_naive_utc(),
            tz=payload.recurrence.tz,
        )
```

Replace the delivery block (everything from `body = (text or "").strip()...` through `_log.info("scheduler.digest.delivered", job_id=job_id)` at the end of the success path) with:

```python
        body = (text or "").strip() or _DIGEST_EMPTY_RU
        run_id = f"digest-{uuid4().hex[:12]}"
        try:
            receipt = await deliver_output(
                sender=sender,
                chat_id=chat_id,
                telegram_id=owner_id,
                wiki_id=primary_id,
                run_id=run_id,
                text=body,
                runs_dir=primary_path / "data" / "runs",
                audit_session_maker=audit_maker,
                kind="digest",
                job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001 — scheduler/event loop must survive
            await _digest_strike(session, scheduler, job, exc=exc)
            return

        job.retry_count = 0
        job.finished_at_utc = _now_naive_utc()
        # status stays 'scheduled' — recurring.
        await session.commit()
        _log.info(
            "scheduler.digest.delivered",
            job_id=job_id,
            run_id=run_id,
            n_messages=receipt.n_messages,
            document_sent=receipt.document_sent,
        )
```

(The empty-WIKI branch with `_DIGEST_NO_WIKI_RU` stays exactly as it is — plain `sender.send_message`, mark finished, no strike. The `bad payload` / `wrong payload kind` branches stay unchanged.)

- [ ] **Step 4: Run the digest tests — expect PASS**

Run: `uv run pytest tests/unit/scheduler/test_firing.py -k digest -v`
Expected: PASS (all digest tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/ai_steward_wiki/scheduler/firing.py tests/unit/scheduler/test_firing.py
git commit -m "feat(M-SCHEDULER-FIRING): route fire_digest_job through tg.output.deliver_output(kind=digest); set_digest_context +audit_session_maker (aisw-w3k)"
```

---

## Task 3: digest-flavoured `deliver_output` tests (FR-2 coverage)

**Files:**
- Test: `tests/unit/tg/test_output.py`

- [ ] **Step 1: Add the two digest tests** — append to `tests/unit/tg/test_output.py`:

```python
async def test_deliver_digest_splits_at_b_headers(tmp_path, audit_session_maker) -> None:
    sender = FakeSender()
    # Three <b> sections; total length pushes past INLINE_THRESHOLD but under CHAIN_THRESHOLD.
    pad = "строка наполнителя. " * 80
    text = (
        f"<b>📌 TL;DR</b>\nкоротко.\n\n"
        f"<b>📅 Сегодня</b>\n{pad}\n\n"
        f"<b>💊 Лекарства</b>\n{pad}\n"
    )
    assert 3500 < len(text) <= 10000
    receipt = await deliver_output(
        sender=sender,
        chat_id=10,
        telegram_id=7,
        wiki_id="health",
        run_id="digest-test1",
        text=text,
        runs_dir=tmp_path / "runs",
        audit_session_maker=audit_session_maker,
        kind="digest",
    )
    assert 2 <= receipt.n_messages <= 3
    assert receipt.document_sent is False
    msgs = [m for (_cid, m) in sender.sent]
    # Each part carries an (i/M) footer.
    for i, m in enumerate(msgs, start=1):
        assert m.rstrip().endswith(f"({i}/{len(msgs)})")
    # A split happened at a <b> header — the 2nd message starts a new section.
    assert msgs[1].lstrip().startswith("<b>")


async def test_deliver_digest_large_to_document(tmp_path, audit_session_maker) -> None:
    sender = FakeSender()
    text = "<b>📌 TL;DR</b>\n" + ("очень длинная сводка. " * 700)  # > 10000
    assert len(text) > 10000
    receipt = await deliver_output(
        sender=sender,
        chat_id=10,
        telegram_id=7,
        wiki_id="health",
        run_id="digest-test2",
        text=text,
        runs_dir=tmp_path / "runs",
        audit_session_maker=audit_session_maker,
        kind="digest",
    )
    assert receipt.document_sent is True
    assert receipt.summary_chars is not None
    assert sender.documents  # FakeSender recorded a send_document call
```

   (If `FakeSender` does not expose `.documents`, use whatever attribute it records `send_document` calls under — check `tests/unit/tg/conftest.py`. The other large-output test `test_deliver_summary_plus_document_for_large` already exercises `send_document`; mirror its assertion style.)

- [ ] **Step 2: Run — expect PASS (no production code change; ChainSplitter already does this)**

Run: `uv run pytest tests/unit/tg/test_output.py -k digest -v`
Expected: PASS (2 tests). If `test_deliver_digest_splits_at_b_headers` fails on the `msgs[1].lstrip().startswith("<b>")` assertion, relax it to `assert "<b>" in msgs[1]` and note in the commit that the split landed inside the padded section — still a `<b>`-boundary-prioritised cut.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/tg/test_output.py
git commit -m "test(M-TG-TEXT): digest deliver_output — <b>-header split with (i/M) + >10000 send_document (aisw-w3k)"
```

---

## Task 4: rewrite `prompts/digest.md` to the D-024 contract

**Files:**
- Modify: `prompts/digest.md`

- [ ] **Step 1: Replace the whole file with:**

```markdown
semver: 0.1.0

Ты — персональный ассистент. Тебе дали read-доступ (через `--add-dir`) к одной или нескольким папкам `<Имя>-WIKI/` пользователя. В сообщении пользователя есть блок «Запланировано на ближайшие N ч …» (или «На ближайшие N ч ничего не запланировано.») — учитывай его.

Задача: собрать компактную сводку на русском, удобную для чтения с телефона.

Формат строго такой:

1. Первая секция — всегда `<b>📌 TL;DR</b>`, под ней 3–5 строк самого важного.
2. Затем — только те секции, в которых есть содержимое, каждая со своим заголовком:
   - `<b>📅 Сегодня</b>` — события и дела на сегодня (используй блок «Запланировано…»);
   - `<b>💊 Лекарства</b>` — приёмы лекарств;
   - `<b>📈 Трекеры</b>` — сон, шаги, вес и т.п.;
   - `<b>📝 Обновления WIKI</b>` — что изменилось в WIKI за период.
3. Каждый пункт — одна короткая строка. Без воды. Без markdown-таблиц.
4. Разметка — только разрешённый HTML Telegram: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a href="…">`, `<blockquote>`. В обычном тексте экранируй `<`, `>`, `&` как `&lt;`, `&gt;`, `&amp;`. **Не используй MarkdownV2.**
5. Если делать нечего и обновлений нет — ответь ровно одной строкой: `🌿 Сегодня дел нет.` (без заголовков).

Только читай и суммируй. Не редактируй файлы.
```

- [ ] **Step 2: Sanity-check the prompt is well-formed**

Run: `uv run python -c "import pathlib; t=pathlib.Path('prompts/digest.md').read_text(); assert t.startswith('semver: 0.1.0'); assert '📌 TL;DR' in t; assert 'MarkdownV2' in t; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add prompts/digest.md
git commit -m "feat(M-RUNTIME-WIRING): prompts/digest.md — D-024 HTML+TL;DR contract (aisw-w3k)"
```

---

## Task 5: wire `audit_session_maker` into `set_digest_context` at startup

**Files:**
- Modify: `src/ai_steward_wiki/__main__.py` (the `firing.set_digest_context(...)` call ~line 966; change-summary header)

- [ ] **Step 1: Add the kwarg**

Change the call to:

```python
    firing.set_digest_context(
        scheduler=scheduler,
        runner=digest_runner_adapter,
        resolve_owner_wikis=_resolve_owner_wikis_factory(settings.wiki_root),
        jobs_session_maker=jobs_maker,
        audit_session_maker=audit_maker,
        sender=sender,
    )
```

- [ ] **Step 2: Bump the `__main__` change-summary** — in the `# START_CHANGE_SUMMARY` block at the top of `__main__.py`, add a new `LAST_CHANGE` line (and demote the current one to `PREVIOUS`):

```
#   LAST_CHANGE: v0.5.1 - aisw-w3k (Inbox-WIKI Phase-D.b.2a): digest delivery routed
#                through tg.output.deliver_output — firing.set_digest_context(...) now
#                also gets audit_session_maker=audit_maker.
```

- [ ] **Step 3: Smoke-import `__main__` to catch wiring typos**

Run: `uv run python -c "import ai_steward_wiki.__main__ as m; print('import ok')"`
Expected: `import ok` (no exception). If it complains about a missing kwarg or undefined name, fix before committing.

- [ ] **Step 4: Commit**

```bash
git add src/ai_steward_wiki/__main__.py
git commit -m "feat(M-RUNTIME-WIRING): pass audit_session_maker to firing.set_digest_context (aisw-w3k)"
```

---

## Task 6: update `firing.py` GRACE header (contract + map + version)

**Files:**
- Modify: `src/ai_steward_wiki/scheduler/firing.py` (the `# START_MODULE_CONTRACT` … `# END_CHANGE_SUMMARY` block at the top)

- [ ] **Step 1: Edit the header**

- `# VERSION: 0.2.0` → `# VERSION: 0.3.0`.
- In `DEPENDS:` add `ai_steward_wiki.tg.output.deliver_output` to the list.
- In `# START_MODULE_MAP` change the `set_digest_context` line to:
  `#   set_digest_context - install the digest registry (scheduler, runner, owner-WIKI resolver, jobs+audit sessionmakers, sender)`
  and add a line:
  `#   _build_planner_context - query jobs.db for the owner's in-window scheduled jobs → ru planner block (module-private)`
- In `# START_CHANGE_SUMMARY` add a new `LAST_CHANGE` (demote the current to `PREVIOUS`):

```
#   LAST_CHANGE: v0.3.0 - aisw-w3k (Phase-D.b.2a): fire_digest_job delivers via
#                tg.output.deliver_output(kind='digest') — D-024/D-025 (<b>-section
#                split + (n/m) + send_document fallthrough + data/runs/ persist +
#                audit.run_outputs row); set_digest_context +audit_session_maker;
#                _build_planner_context replaces the one-line planner stub; dropped
#                _DIGEST_TG_LIMIT.
```

- [ ] **Step 2: `make lint` — expect clean**

Run: `make lint`
Expected: `ruff check` ✅, `ruff format --check` ✅, `mypy src` ✅, `grace lint` ✅. Fix any drift (ruff format may want a reflow — run `uv run ruff format src/ai_steward_wiki/scheduler/firing.py` and re-stage).

- [ ] **Step 3: Commit**

```bash
git add src/ai_steward_wiki/scheduler/firing.py
git commit -m "docs(M-SCHEDULER-FIRING): refresh module header for digest deliver_output routing (aisw-w3k)"
```

---

## Task 7: full verification

- [ ] **Step 1: Run the touched test modules**

Run: `uv run pytest tests/unit/scheduler/test_firing.py tests/unit/tg/test_output.py tests/unit/tg/test_digest_e2e.py -v`
Expected: all PASS. (`test_digest_e2e.py` exercises `set_digest_context` end-to-end via `__main__`-style wiring — if it constructs `set_digest_context` directly it needs the `audit_session_maker` kwarg added too; update it the same way as `test_firing.py` if it fails.)

- [ ] **Step 2: Full unit suite + coverage**

Run: `make total-test` (or, if that pulls integration: `uv run pytest tests/unit --cov=ai_steward_wiki --cov-report=term-missing`)
Expected: green; core coverage ≥80%; no new lint/grace failures.

- [ ] **Step 3: Final lint gate**

Run: `make lint`
Expected: all ✅.

- [ ] **Step 4: Commit any residual formatting**

```bash
git add -A
git commit -m "chore(M-SCHEDULER-FIRING): formatting after digest presentation core (aisw-w3k)"
```

(Skip if nothing changed.)

---

## Finish (handled by feature-workflow Step 13, not a task here)

- `grace-refresh` (full) — regenerate `knowledge-graph.xml` / `verification-plan.xml` / `development-plan.xml`.
- `_adr` — write `docs/adr/ADR-024-digest-presentation.md` (records design SD-1..SD-3 + rejected alternatives: separate digest splitter [DRY], HaikuSummarizer in the digest registry [YAGNI], `scheduler/digest_planner.py` module [KISS]).
- Tick the "перенос в ADR" checkbox in `docs/Spec-WIKI/decisions/D-024-digest-format.md` → `docs/adr/ADR-024-digest-presentation.md`.
- `_report` — `docs/reports/20260512-inbox-wiki-digest-presentation-report.md` (major-ish — include the FR coverage table and test evidence).
- `smart-commit` the meta files.
- `bd close aisw-w3k --reason="Phase-D.b.2a complete — digest delivery via deliver_output(kind=digest), real planner query, D-024 prompt; FR-1..4,10 done; FR-5..9 → aisw-269"`.
- `bd dolt push`.

---

## Self-Review

**Spec coverage (vs design `covers_fr: [FR-1, FR-2, FR-3, FR-4, FR-10]`):**
- FR-1 (deliver_output(kind=digest), D-025 hybrid, persist, audit row) → Task 2 + Task 5. ✅
- FR-2 (`<b>`-section split + `(n/m)` + send_document fallthrough) → already in `ChainSplitter`/`deliver_output`; covered by Task 3 tests. ✅
- FR-3 (prompts/digest.md TL;DR-section + sections + empty line, HTML) → Task 4. ✅
- FR-4 (real jobs.db planner-window query) → Task 1 + Task 2 (wired into `fire_digest_job`). ✅
- FR-10a (ADR-024 + GRACE refresh + verification-plan log anchors) → Task 6 (header) + Finish (ADR, grace-refresh). ✅
- FR-5..9 — explicitly out of scope (→ `aisw-269`); no task. Correct per the split.

**Placeholder scan:** no "TBD"/"add error handling"/"similar to Task N" — every code step shows the actual code/command. The one soft spot — Task 3 Step 1's `FakeSender.documents` attribute name and Task 7's `test_digest_e2e.py` shape — both carry an explicit "check the file / mirror the existing test" instruction with a fallback, not a blind placeholder.

**Type consistency:** `_build_planner_context(session, *, owner_telegram_id, window_hours, now_utc, tz) -> str` — same signature in Task 1 (def + tests) and Task 2 (call site, with `now_utc=_now_naive_utc()`, `tz=payload.recurrence.tz`). `_digest_ctx` 6-tuple order `(scheduler, runner, resolve_owner_wikis, jobs_session_maker, audit_session_maker, sender)` — identical in the type alias, `set_digest_context` body, and the `fire_digest_job` unpack. `deliver_output(...)` kwargs match the real signature in `tg/output.py` (`sender, chat_id, telegram_id, wiki_id, run_id, text, runs_dir, audit_session_maker, kind, job_id`). `receipt.n_messages` / `receipt.document_sent` / `receipt.summary_chars` match `DeliveryReceipt`.
