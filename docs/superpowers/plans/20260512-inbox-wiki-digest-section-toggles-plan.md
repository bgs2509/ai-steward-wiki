# Inbox-WIKI per-user digest section toggles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a digest-job owner permanently silence the `📈 Трекеры` and/or `📝 Обновления WIKI` sections of their recurring digest via a `/digest_sections` command, persisted per `telegram_id`, honoured at digest fire time.

**Architecture:** New `user_digest_prefs` table in `sessions.db` (one row per owner, two boolean columns, FK `users.user_id ON DELETE CASCADE`), reached by the first incremental `sessions.db` migration (`0002`, an idempotent `Base.metadata.create_all` delta). A new third slash command `/digest_sections` renders an inline keyboard of toggle buttons (`digestsec:<key>:<target>` callbacks, message edited in place); `scheduler.firing.fire_digest_job` reads the owner's prefs and — only when something is off — appends `«Не включай разделы: …»` to the `planner_context` string, with one explanatory sentence added to `prompts/digest.md`. `/expand` is untouched (an explicit request overrides the toggle). New ADR-026; GRACE refresh.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 async, Alembic (per-DB), aiogram 3.x (`Command` filter + `callback_query`), pytest, structlog. No new third-party dependency.

**SSoT:** discovery `docs/superpowers/specs/20260512-inbox-wiki-digest-section-toggles-discovery.md`; design `docs/superpowers/specs/20260512-inbox-wiki-digest-section-toggles-design.md` (TD-1…8). bd: `aisw-pv8`.

---

## File structure

**New files:**
- `src/ai_steward_wiki/storage/sessions/digest_prefs.py` — `DigestPrefs` dataclass, `TOGGLEABLE_DIGEST_SECTIONS`, `SECTION_DISPLAY_NAME`, `get_digest_prefs`, `set_digest_section`.
- `alembic/sessions/versions/0002_user_digest_prefs.py` — idempotent `create_all` upgrade, explicit `drop_table` downgrade.
- `tests/unit/storage/test_digest_prefs.py`
- `tests/unit/tg/test_digest_sections.py`
- `docs/adr/ADR-026-digest-section-toggles.md`

**Modified files:**
- `src/ai_steward_wiki/storage/sessions/models.py` — add `UserDigestPrefs` ORM model.
- `src/ai_steward_wiki/scheduler/firing.py` — `set_digest_context` + `_digest_ctx` 7-tuple (add `sessions_session_maker`); `get_owner_digest_prefs` / `set_owner_digest_section` accessors; `fire_digest_job` directive injection.
- `src/ai_steward_wiki/tg/handlers.py` — `Command("digest_sections")` handler, `digestsec:` `callback_query` handler, `_build_sections_kb`, `parse_digestsec_callback`, ru strings.
- `src/ai_steward_wiki/__main__.py` — pass the sessions sessionmaker into `firing.set_digest_context(...)`.
- `prompts/digest.md` — one sentence, semver `0.1.0 → 0.1.1`.
- `tests/unit/storage/test_baselines.py` — `user_digest_prefs` in the sessions expected-tables set; stepwise migration test.
- `tests/unit/scheduler/test_firing.py` — directive-injection tests.
- `docs/knowledge-graph.xml`, `docs/verification-plan.xml`, `docs/development-plan.xml` — via `grace-refresh` (Task 8).
- `docs/20260408_changelog.md` — feature entry (Task 8).

---

### Task 1: `digest_prefs` storage module + `UserDigestPrefs` model

**Files:**
- Create: `src/ai_steward_wiki/storage/sessions/digest_prefs.py`
- Modify: `src/ai_steward_wiki/storage/sessions/models.py` (add `UserDigestPrefs`, bump `VERSION` to `0.0.3`, update `MODULE_MAP` + `CHANGE_SUMMARY`)
- Test: `tests/unit/storage/test_digest_prefs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/storage/test_digest_prefs.py
"""user_digest_prefs repo: defaults, toggle, unknown-user, CASCADE."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.storage.sessions.digest_prefs import (
    SECTION_DISPLAY_NAME,
    TOGGLEABLE_DIGEST_SECTIONS,
    DigestPrefs,
    get_digest_prefs,
    set_digest_section,
)
from ai_steward_wiki.storage.sessions.engine import Base
from ai_steward_wiki.storage.sessions.models import User, UserDigestPrefs


@pytest.fixture
async def session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _add_user(maker: async_sessionmaker, telegram_id: int) -> int:
    async with maker() as s:
        u = User(
            telegram_id=telegram_id,
            role="user",
            display_name="t",
            tz="Europe/Moscow",
            enabled=True,
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
            updated_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(u)
        await s.commit()
        return u.user_id


def test_vocab_subset_of_expand_keys():
    from ai_steward_wiki.tg.handlers import EXPAND_SECTION_KEYS

    assert set(TOGGLEABLE_DIGEST_SECTIONS) <= set(EXPAND_SECTION_KEYS)
    assert set(SECTION_DISPLAY_NAME) == set(TOGGLEABLE_DIGEST_SECTIONS)


async def test_get_defaults_when_no_row(session_maker):
    await _add_user(session_maker, 111)
    prefs = await get_digest_prefs(session_maker, 111)
    assert prefs == DigestPrefs(trackers_enabled=True, wiki_enabled=True)
    assert prefs.disabled_keys == ()


async def test_get_defaults_when_no_user(session_maker):
    prefs = await get_digest_prefs(session_maker, 999)
    assert prefs == DigestPrefs(trackers_enabled=True, wiki_enabled=True)


async def test_set_then_get_roundtrip(session_maker):
    await _add_user(session_maker, 222)
    after = await set_digest_section(session_maker, 222, section="trackers", enabled=False)
    assert after == DigestPrefs(trackers_enabled=False, wiki_enabled=True)
    assert after.disabled_keys == ("trackers",)
    assert await get_digest_prefs(session_maker, 222) == after
    again = await set_digest_section(session_maker, 222, section="wiki", enabled=False)
    assert again == DigestPrefs(trackers_enabled=False, wiki_enabled=False)
    assert again.disabled_keys == ("trackers", "wiki")
    back = await set_digest_section(session_maker, 222, section="trackers", enabled=True)
    assert back == DigestPrefs(trackers_enabled=True, wiki_enabled=False)


async def test_set_unknown_section_raises(session_maker):
    await _add_user(session_maker, 333)
    with pytest.raises(ValueError):
        await set_digest_section(session_maker, 333, section="bogus", enabled=False)


async def test_set_for_unknown_user_is_noop(session_maker):
    after = await set_digest_section(session_maker, 4040, section="wiki", enabled=False)
    assert after == DigestPrefs(trackers_enabled=True, wiki_enabled=True)
    async with session_maker() as s:
        from sqlalchemy import select

        rows = (await s.execute(select(UserDigestPrefs))).scalars().all()
    assert rows == []


async def test_cascade_delete(session_maker):
    uid = await _add_user(session_maker, 555)
    await set_digest_section(session_maker, 555, section="trackers", enabled=False)
    async with session_maker() as s:
        await s.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))
        u = await s.get(User, uid)
        await s.delete(u)
        await s.commit()
    assert await get_digest_prefs(session_maker, 555) == DigestPrefs(True, True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/storage/test_digest_prefs.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_steward_wiki.storage.sessions.digest_prefs` / `cannot import name 'UserDigestPrefs'`.

- [ ] **Step 3: Add the `UserDigestPrefs` model**

In `src/ai_steward_wiki/storage/sessions/models.py`, after the `InboxHintCache` class (before `FsmState`), add:

```python
class UserDigestPrefs(Base):
    """Per-owner on/off for the optional digest sections (ADR-025/ADR-026).

    Absent row ⇒ everything enabled (opt-out feature). Only `trackers` and
    `wiki` are toggleable; TL;DR and `today` are always shown.
    """

    __tablename__ = "user_digest_prefs"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    trackers_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1")
    )
    wiki_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1")
    )
    updated_at_utc: Mapped[datetime] = mapped_column(nullable=False)
```

Ensure `Boolean` and `text` are imported from `sqlalchemy` at the top of the file (add to the existing `from sqlalchemy import (...)` block if missing). Update the header: `VERSION: 0.0.3`; add to `MODULE_MAP` the line `#   UserDigestPrefs - per-owner digest section toggles (trackers/wiki on-off, ADR-026)`; prepend a `CHANGE_SUMMARY` `LAST_CHANGE: v0.0.3 - aisw-pv8: UserDigestPrefs (per-user digest section toggles)`. Add `user_digest_prefs` to the `SCOPE:` line of the MODULE_CONTRACT.

- [ ] **Step 4: Write `digest_prefs.py`**

```python
# FILE: src/ai_steward_wiki/storage/sessions/digest_prefs.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Read/write a digest-job owner's per-section toggles (user_digest_prefs).
#   SCOPE: get_digest_prefs (read, defaults when absent), set_digest_section (upsert one section).
#   DEPENDS: SQLAlchemy.asyncio, ai_steward_wiki.storage.sessions.models.{User,UserDigestPrefs},
#            ai_steward_wiki.storage.sessions.users.resolve_user_id
#   LINKS: M-STORAGE-SESSIONS, ADR-025, ADR-026, D-024, aisw-pv8
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TOGGLEABLE_DIGEST_SECTIONS - the section keys a user may toggle ('trackers','wiki')
#   SECTION_DISPLAY_NAME - section key -> ru label with emoji (for buttons + the digest directive)
#   DigestPrefs - frozen view of one owner's toggles (+ disabled_keys)
#   get_digest_prefs - telegram_id -> DigestPrefs (both True when no row / no user)
#   set_digest_section - upsert one section's bool for telegram_id; no-op (returns defaults) if no users row
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-pv8: initial per-user digest section toggles repo
# END_CHANGE_SUMMARY

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.sessions.models import UserDigestPrefs
from ai_steward_wiki.storage.sessions.users import resolve_user_id

__all__ = [
    "SECTION_DISPLAY_NAME",
    "TOGGLEABLE_DIGEST_SECTIONS",
    "DigestPrefs",
    "get_digest_prefs",
    "set_digest_section",
]

# The two optional digest sections (ADR-025). Order is the canonical display order.
TOGGLEABLE_DIGEST_SECTIONS: tuple[str, ...] = ("trackers", "wiki")

# ru label with emoji — mirrors the <b>-headers in prompts/digest.md.
SECTION_DISPLAY_NAME: dict[str, str] = {
    "trackers": "📈 Трекеры",
    "wiki": "📝 Обновления WIKI",
}

# Column name on UserDigestPrefs for each key (DRY: one mapping, used by get/set).
_SECTION_COLUMN: dict[str, str] = {"trackers": "trackers_enabled", "wiki": "wiki_enabled"}


@dataclass(frozen=True, slots=True)
class DigestPrefs:
    trackers_enabled: bool = True
    wiki_enabled: bool = True

    @property
    def disabled_keys(self) -> tuple[str, ...]:
        return tuple(
            k for k in TOGGLEABLE_DIGEST_SECTIONS if not getattr(self, _SECTION_COLUMN[k])
        )


def _from_row(row: UserDigestPrefs | None) -> DigestPrefs:
    if row is None:
        return DigestPrefs()
    return DigestPrefs(
        trackers_enabled=bool(row.trackers_enabled), wiki_enabled=bool(row.wiki_enabled)
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# START_CONTRACT: get_digest_prefs
#   PURPOSE: Read a telegram_id's digest section toggles.
#   INPUTS: { session_maker: async_sessionmaker[AsyncSession], telegram_id: int }
#   OUTPUTS: { DigestPrefs - both True if no users row or no prefs row }
#   SIDE_EFFECTS: one read-only DB transaction
#   LINKS: M-STORAGE-SESSIONS, ADR-026
# END_CONTRACT: get_digest_prefs
async def get_digest_prefs(
    session_maker: async_sessionmaker[AsyncSession], telegram_id: int
) -> DigestPrefs:
    user_id = await resolve_user_id(session_maker, telegram_id)
    if user_id is None:
        return DigestPrefs()
    async with session_maker() as session:
        row = await session.get(UserDigestPrefs, user_id)
        return _from_row(row)


# START_CONTRACT: set_digest_section
#   PURPOSE: Set one section's on/off for a telegram_id (creating the row with defaults if absent).
#   INPUTS: { session_maker, telegram_id: int, section: str (must be in TOGGLEABLE_DIGEST_SECTIONS), enabled: bool }
#   OUTPUTS: { DigestPrefs - the new state; both True (no write) if telegram_id has no users row }
#   SIDE_EFFECTS: one read-write DB transaction (upsert)
#   LINKS: M-STORAGE-SESSIONS, ADR-026
# END_CONTRACT: set_digest_section
async def set_digest_section(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    section: str,
    enabled: bool,
) -> DigestPrefs:
    if section not in _SECTION_COLUMN:
        raise ValueError(f"unknown digest section: {section!r}")
    user_id = await resolve_user_id(session_maker, telegram_id)
    if user_id is None:
        return DigestPrefs()
    async with session_maker() as session:
        row = await session.get(UserDigestPrefs, user_id)
        if row is None:
            row = UserDigestPrefs(user_id=user_id, trackers_enabled=True, wiki_enabled=True)
            session.add(row)
        setattr(row, _SECTION_COLUMN[section], enabled)
        row.updated_at_utc = _now()
        await session.commit()
        return DigestPrefs(
            trackers_enabled=bool(row.trackers_enabled), wiki_enabled=bool(row.wiki_enabled)
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/storage/test_digest_prefs.py -q`
Expected: PASS (all cases). Then `uv run mypy src/ai_steward_wiki/storage/sessions/digest_prefs.py` → no issues.

- [ ] **Step 6: Commit**

```bash
git add src/ai_steward_wiki/storage/sessions/digest_prefs.py src/ai_steward_wiki/storage/sessions/models.py tests/unit/storage/test_digest_prefs.py
git commit -m "feat(M-STORAGE-SESSIONS): user_digest_prefs model + digest_prefs repo (aisw-pv8)"
```

---

### Task 2: Alembic `0002` migration + baseline test

**Files:**
- Create: `alembic/sessions/versions/0002_user_digest_prefs.py`
- Modify: `tests/unit/storage/test_baselines.py`

- [ ] **Step 1: Add the failing test**

In `tests/unit/storage/test_baselines.py`, change the `sessions` expected-tables set to include `"user_digest_prefs"`:

```python
        (
            "sessions",
            "AISW_SESSIONS_DB_URL_SYNC",
            {"users", "pending_users", "pending_confirms", "inbox_hint_cache", "fsm", "user_digest_prefs"},
        ),
```

And append a new test to the file:

```python
def test_sessions_stepwise_upgrade_creates_user_digest_prefs(tmp_path, monkeypatch):
    """Stamp 0001 then upgrade to head → 0002 brings user_digest_prefs onto an already-baselined DB."""
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "0001_sessions_baseline")
    command.upgrade(cfg, "head")

    import sqlite3

    conn = sqlite3.connect(db_path)
    rows = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "user_digest_prefs" in rows
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/storage/test_baselines.py -q`
Expected: FAIL — `missing tables in sessions.db: {'user_digest_prefs'}` for the parametrized test; the stepwise test errors on `command.upgrade(cfg, "head")` with `Can't locate revision 'head'` only-baseline → actually it fails the final assert (`user_digest_prefs` not in rows) because `0002` doesn't exist yet.

- [ ] **Step 3: Write the migration**

```python
# alembic/sessions/versions/0002_user_digest_prefs.py
"""sessions.db: add user_digest_prefs (per-user digest section toggles).

First incremental migration past 0001_sessions_baseline. The baseline's
upgrade() is Base.metadata.create_all of LIVE metadata, so on a fresh DB the
table is already created by the baseline step; this migration's upgrade() is
therefore idempotent (create_all with checkfirst=True) — it only does work on
a DB that was baselined before UserDigestPrefs existed. downgrade() drops the
table explicitly. Convention recorded in ADR-026.

Revision ID: 0002_user_digest_prefs
Revises: 0001_sessions_baseline
Create Date: 2026-05-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from ai_steward_wiki.storage.sessions import models  # noqa: F401
from ai_steward_wiki.storage.sessions.engine import Base

revision: str = "0002_user_digest_prefs"
down_revision: str | None = "0001_sessions_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: creates user_digest_prefs if missing, no-op otherwise.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    op.drop_table("user_digest_prefs")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/storage/test_baselines.py -q`
Expected: PASS — both the parametrized `sessions` case and `test_sessions_stepwise_upgrade_creates_user_digest_prefs`.

- [ ] **Step 5: Commit**

```bash
git add alembic/sessions/versions/0002_user_digest_prefs.py tests/unit/storage/test_baselines.py
git commit -m "feat(M-STORAGE-SESSIONS): alembic/sessions/0002 — user_digest_prefs migration (aisw-pv8)"
```

---

### Task 3: `firing` — sessions sessionmaker in the digest context + prefs accessors

**Files:**
- Modify: `src/ai_steward_wiki/scheduler/firing.py` (bump `VERSION`, update `MODULE_CONTRACT`/`MODULE_MAP`/`CHANGE_SUMMARY`)
- Test: `tests/unit/scheduler/test_firing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/scheduler/test_firing.py` (it already has a `session_factory` jobs fixture and the autouse context-reset fixture; add a sessions sessionmaker fixture and tests):

```python
@pytest.fixture
async def sessions_factory(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from ai_steward_wiki.storage.sessions.engine import Base as SBase

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SBase.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_user(sessions_maker, telegram_id):
    from datetime import UTC, datetime

    from ai_steward_wiki.storage.sessions.models import User

    async with sessions_maker() as s:
        s.add(
            User(
                telegram_id=telegram_id, role="user", display_name="t", tz="Europe/Moscow",
                enabled=True, created_at_utc=datetime.now(UTC).replace(tzinfo=None),
                updated_at_utc=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await s.commit()


async def test_owner_digest_prefs_accessors(sessions_factory):
    from ai_steward_wiki.scheduler import firing

    await _seed_user(sessions_factory, 70)
    firing.set_digest_context(
        scheduler=_FakeScheduler(), runner=_dummy_runner, resolve_owner_wikis=_dummy_resolver,
        jobs_session_maker=sessions_factory, audit_session_maker=sessions_factory,
        sender=_FakeSender(), sessions_session_maker=sessions_factory,
    )
    assert (await firing.get_owner_digest_prefs(70)).disabled_keys == ()
    after = await firing.set_owner_digest_section(70, section="trackers", enabled=False)
    assert after.disabled_keys == ("trackers",)
    assert (await firing.get_owner_digest_prefs(70)).disabled_keys == ("trackers",)
```

Where `_dummy_runner` / `_dummy_resolver` are tiny module-level helpers in the test file (add if not present):
```python
async def _dummy_runner(**_kw):  # noqa: ANN003
    return "x"


async def _dummy_resolver(_owner):  # noqa: ANN001
    return []
```
(If the test file already has equivalents, reuse them.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/scheduler/test_firing.py -q -k owner_digest_prefs`
Expected: FAIL — `set_digest_context() got an unexpected keyword argument 'sessions_session_maker'` / `module 'ai_steward_wiki.scheduler.firing' has no attribute 'get_owner_digest_prefs'`.

- [ ] **Step 3: Implement in `firing.py`**

3a. Add the import near the other storage imports:
```python
from ai_steward_wiki.storage.sessions.digest_prefs import (
    SECTION_DISPLAY_NAME,
    DigestPrefs,
    get_digest_prefs,
    set_digest_section,
)
```

3b. Change `_digest_ctx` to a 7-tuple (add `sessions_session_maker` last):
```python
# tuple: (scheduler, runner, resolve_owner_wikis, jobs_session_maker, audit_session_maker, sender, sessions_session_maker)
_digest_ctx: (
    tuple[
        AsyncIOScheduler,
        DigestRunner,
        Callable[[int], Awaitable[Sequence[tuple[str, Path]]]],
        async_sessionmaker[AsyncSession],
        async_sessionmaker[AsyncSession],
        TgSender,
        async_sessionmaker[AsyncSession],
    ]
    | None
) = None
```

3c. `set_digest_context` — add the parameter and store it:
```python
def set_digest_context(
    *,
    scheduler: AsyncIOScheduler,
    runner: DigestRunner,
    resolve_owner_wikis: Callable[[int], Awaitable[Sequence[tuple[str, Path]]]],
    jobs_session_maker: async_sessionmaker[AsyncSession],
    audit_session_maker: async_sessionmaker[AsyncSession],
    sender: TgSender,
    sessions_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Install the digest firing registry. Call once at startup."""
    global _digest_ctx
    _digest_ctx = (
        scheduler, runner, resolve_owner_wikis,
        jobs_session_maker, audit_session_maker, sender, sessions_session_maker,
    )
```

3d. Update **every** `= _digest_ctx` unpack site to the 7-tuple form. Search for `_digest_ctx` in the file; the existing unpacks (in `list_owner_digest_job_ids`, `run_section_expand`, `fire_digest_job`, and any strike/helper) currently have 6 names — add a trailing `_` (or bind the name where used). Example: `_, _, _, maker, _, _ = _digest_ctx` → `_, _, _, maker, _, _, _ = _digest_ctx`.

3e. Add the two accessors (place near `list_owner_digest_job_ids`):
```python
async def get_owner_digest_prefs(owner_telegram_id: int) -> DigestPrefs:
    """The owner's digest section toggles (DigestPrefs(True,True) if unset). For /digest_sections."""
    if _digest_ctx is None:
        raise DigestNotInitialisedError(
            "digest context not initialised — call set_digest_context() at startup"
        )
    sessions_maker = _digest_ctx[6]
    return await get_digest_prefs(sessions_maker, owner_telegram_id)


async def set_owner_digest_section(
    owner_telegram_id: int, *, section: str, enabled: bool
) -> DigestPrefs:
    """Flip one digest section for the owner; returns the new DigestPrefs. For the digestsec: callback."""
    if _digest_ctx is None:
        raise DigestNotInitialisedError(
            "digest context not initialised — call set_digest_context() at startup"
        )
    sessions_maker = _digest_ctx[6]
    return await set_digest_section(
        sessions_maker, owner_telegram_id, section=section, enabled=enabled
    )
```

3f. Header maintenance: bump `VERSION` (e.g. `0.2.0 → 0.3.0`); in `MODULE_CONTRACT` `SCOPE:` add `get_owner_digest_prefs/set_owner_digest_section (digest section toggles — aisw-pv8)` and add `set_digest_section`/`get_digest_prefs` deps; `DEPENDS:` add `ai_steward_wiki.storage.sessions.digest_prefs`; `LINKS:` add `ADR-026`, `aisw-pv8`; add `MODULE_MAP` lines for the two new accessors; prepend a `CHANGE_SUMMARY` entry.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/scheduler/test_firing.py -q && uv run mypy src/ai_steward_wiki/scheduler/firing.py`
Expected: PASS; mypy clean. (Run the whole `test_firing.py` — the 6→7-tuple change must not break existing tests; those that call `set_digest_context` will need the new kwarg — see Step 5.)

- [ ] **Step 5: Fix existing callers of `set_digest_context` in tests**

Search the test suite for `set_digest_context(` — every call site (in `test_firing.py`, `tests/unit/tg/test_digest_e2e.py`, and any other) must pass `sessions_session_maker=...`. In e2e tests that already build a sessions sessionmaker, pass it; in firing unit tests that don't care, pass the jobs `session_factory` (or a throwaway sessions maker) — the prefs path tolerates a sessions.db that lacks `user_digest_prefs`? No: `get_digest_prefs` does `session.get(UserDigestPrefs, ...)` which needs the table. So in those tests pass a sessionmaker over a DB with `Base.metadata.create_all` from `storage.sessions.engine`. Add a shared `sessions_factory` fixture (as in Step 1) and thread it through. Re-run the full suite:

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_steward_wiki/scheduler/firing.py tests/unit/scheduler/test_firing.py tests/unit/tg/test_digest_e2e.py
git commit -m "feat(M-SCHEDULER-FIRING): sessions sessionmaker in digest context + owner digest-prefs accessors (aisw-pv8)"
```

---

### Task 4: `fire_digest_job` — honour the toggles (directive injection)

**Files:**
- Modify: `src/ai_steward_wiki/scheduler/firing.py`
- Test: `tests/unit/scheduler/test_firing.py` and/or `tests/unit/tg/test_digest_e2e.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/scheduler/test_firing.py` (these need a digest job row + a runner spy; the file already has `_insert_job` and a `_DigestRunner`-style spy in `test_digest_e2e.py` — if `test_firing.py` lacks a digest-firing harness, add these tests to `tests/unit/tg/test_digest_e2e.py` instead, next to the existing digest e2e tests, where the harness exists). Sketch:

```python
async def test_digest_no_prefs_planner_context_byte_identical(<digest e2e harness fixtures>):
    # Arrange: an owner with one scheduled digest_job, one *-WIKI dir, NO user_digest_prefs row.
    # Act: await firing.fire_digest_job(job_id)
    # Assert: the runner spy was called once with planner_context == <the exact string the
    #         pre-feature build produced> (i.e. it does NOT contain "Не включай разделы").
    ...
    assert "Не включай разделы" not in runner_spy.calls[0]["planner_context"]


async def test_digest_trackers_disabled_appends_directive(<harness>):
    # Arrange: same, plus set_digest_section(sessions_maker, owner, section="trackers", enabled=False)
    # Act: fire_digest_job(job_id)
    # Assert:
    assert runner_spy.calls[0]["planner_context"].rstrip().endswith("Не включай разделы: 📈 Трекеры.")
    # and the log:
    assert any(e["event"] == "scheduler.digest.sections_filtered" and e["disabled"] == ["trackers"]
               for e in caplog_records)


async def test_digest_both_disabled_lists_both(<harness>):
    # set both off → directive "Не включай разделы: 📈 Трекеры, 📝 Обновления WIKI."
    ...


async def test_digest_prefs_read_failure_degrades_to_all_on(<harness>, monkeypatch):
    # monkeypatch firing.get_digest_prefs to raise; fire_digest_job still delivers,
    # planner_context has no directive.
    monkeypatch.setattr(firing, "get_digest_prefs", _raise)
    await firing.fire_digest_job(job_id)
    assert "Не включай разделы" not in runner_spy.calls[0]["planner_context"]
```

Use the existing digest e2e harness (the `_DigestRunner` spy in `test_digest_e2e.py` records its kwargs — assert on `planner_context`). For the log assertion, use the project's structlog capture fixture (the existing digest tests already assert on log events — follow that pattern).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q -k "digest and (directive or byte_identical or degrades or lists_both)"`
Expected: FAIL — the `trackers_disabled` test fails (no directive appended); `byte_identical` may already pass.

- [ ] **Step 3: Implement in `fire_digest_job`**

Locate, inside `fire_digest_job`, the point where `planner_context` has been built (via `_build_planner_context`) and the WIKI set / `owner_telegram_id` are known, just before the `runner(...)` call. Insert:

```python
# START_BLOCK_DIGEST_SECTION_TOGGLES
try:
    prefs = await get_digest_prefs(_digest_ctx[6], owner_telegram_id)
except Exception:  # degrade-to-all-on: never skip the digest over a prefs read
    _log.warning(
        "scheduler.digest.prefs_read_failed",
        job_id=job_id,
        owner_telegram_id=owner_telegram_id,
    )
    prefs = DigestPrefs()
disabled = prefs.disabled_keys
if disabled:
    _names = ", ".join(SECTION_DISPLAY_NAME[k] for k in disabled)
    planner_context = f"{planner_context}\n\nНе включай разделы: {_names}."
    _log.info(
        "scheduler.digest.sections_filtered",
        job_id=job_id,
        owner_telegram_id=owner_telegram_id,
        disabled=list(disabled),
    )
# END_BLOCK_DIGEST_SECTION_TOGGLES
```

(Use the module's existing logger name — `_log` or `logger`, match the file. Keep `runner(..., section=None)` exactly as today; do not touch the Protocol or the adapter.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit -q`
Expected: PASS (new + existing). `uv run mypy src` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/ai_steward_wiki/scheduler/firing.py tests/
git commit -m "feat(M-SCHEDULER-FIRING): fire_digest_job honours user_digest_prefs (skip disabled sections) (aisw-pv8)"
```

---

### Task 5: `/digest_sections` command + `digestsec:` callback

**Files:**
- Modify: `src/ai_steward_wiki/tg/handlers.py` (bump `VERSION` to `0.2.0`, update `MODULE_CONTRACT`/`MODULE_MAP`/`CHANGE_SUMMARY`, `__all__`)
- Test: `tests/unit/tg/test_digest_sections.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tg/test_digest_sections.py
"""/digest_sections command + digestsec: callback."""

from __future__ import annotations

import pytest

from ai_steward_wiki.tg.handlers import parse_digestsec_callback
from ai_steward_wiki.storage.sessions.digest_prefs import DigestPrefs


def test_parse_digestsec_callback_ok():
    assert parse_digestsec_callback("digestsec:trackers:0") == ("trackers", False)
    assert parse_digestsec_callback("digestsec:wiki:1") == ("wiki", True)


@pytest.mark.parametrize("bad", [
    "digestsec:trackers", "digestsec:trackers:2", "digestsec:bogus:0",
    "confirm:1:cancel", "digestsec:trackers:0:extra", "", "digestsec::0",
])
def test_parse_digestsec_callback_rejects(bad):
    assert parse_digestsec_callback(bad) is None


# Handler-level tests use the project's existing aiogram test harness (FakeSender etc.
# in tests/unit/tg/conftest.py) plus a fake firing context. Build the router via
# build_router(pipeline) after calling firing.set_digest_context(... sessions_session_maker=<a
# sessions sessionmaker over Base.metadata.create_all ...>). Then:
#   - dispatch a /digest_sections Message → assert FakeSender got one message whose
#     reply_markup has 2 inline buttons, texts "📈 Трекеры: вкл ✅" / "📝 Обновления WIKI: вкл ✅",
#     callback_data "digestsec:trackers:0" / "digestsec:wiki:0".
#   - dispatch a CallbackQuery with data "digestsec:trackers:0" → assert the row was written
#     (get_owner_digest_prefs(owner).trackers_enabled is False), edit_message_text/edit_reply_markup
#     was called with the rebuilt keyboard (button now "📈 Трекеры: выкл ⬜", callback "digestsec:trackers:1"),
#     and cb.answer was called.
#   - dispatch a CallbackQuery with garbage data "digestsec:bogus:9" → cb.answer called, no DB write,
#     log "tg.command.digest_sections.bad_callback".
#   - re-dispatch "digestsec:trackers:0" when already off → still off, no error (idempotent).
# Mirror the structure of the existing tests/unit/tg/test_*callback*.py / test_handlers*.py.
```

Fill in the handler-level tests against the actual harness in `tests/unit/tg/conftest.py` and the existing `test_digest_e2e.py` / confirm-callback tests (do not invent a new harness).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/tg/test_digest_sections.py -q`
Expected: FAIL — `cannot import name 'parse_digestsec_callback' from 'ai_steward_wiki.tg.handlers'`.

- [ ] **Step 3: Implement in `handlers.py`**

3a. Imports — add near the top (`firing` is already imported):
```python
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from ai_steward_wiki.storage.sessions.digest_prefs import (
    SECTION_DISPLAY_NAME,
    TOGGLEABLE_DIGEST_SECTIONS,
    DigestPrefs,
)
```
(`Command` and `F` are already imported per the existing `/digest_now` / `/expand` handlers — reuse them. If `InlineKeyboardButton`/`InlineKeyboardMarkup` are already imported via `tg/confirm.py` builders, prefer importing the existing keyboard builder pattern; otherwise import from `aiogram.types` directly.)

3b. Constants + ru strings (near `EXPAND_SECTION_KEYS`):
```python
DIGESTSEC_CALLBACK_PREFIX = "digestsec:"

_DIGEST_SECTIONS_HEADER_RU = "Разделы твоей сводки — нажми, чтобы включить/выключить:"
_DIGESTSEC_DONE_RU = "Готово"
_DIGESTSEC_BAD_RU = "Не понял кнопку."
```

3c. The callback parser (near `parse_confirm_callback`, add to `__all__`):
```python
def parse_digestsec_callback(data: str) -> tuple[str, bool] | None:
    """Parse `digestsec:<section>:<0|1>` → (section, target_enabled), or None if malformed.

    The flag is the TARGET state (what tapping the button sets it to), so a tap on a
    stale message is idempotent.
    """
    if not data.startswith(DIGESTSEC_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    _, section, flag = parts
    if section not in TOGGLEABLE_DIGEST_SECTIONS or flag not in ("0", "1"):
        return None
    return section, flag == "1"
```

3d. The keyboard builder (module-level private):
```python
def _build_digest_sections_kb(prefs: DigestPrefs) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key in TOGGLEABLE_DIGEST_SECTIONS:
        on = getattr(prefs, f"{key}_enabled")
        rows.append([
            InlineKeyboardButton(
                text=f"{SECTION_DISPLAY_NAME[key]}: {'вкл ✅' if on else 'выкл ⬜'}",
                callback_data=f"{DIGESTSEC_CALLBACK_PREFIX}{key}:{0 if on else 1}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

3e. Inside `build_router(pipeline)`, after the existing `Command("digest_now")` / `Command("expand")` handlers, add:
```python
    @router.message(Command("digest_sections"))
    async def _on_digest_sections(message: Message) -> None:
        owner = message.from_user.id if message.from_user else 0
        try:
            prefs = await firing.get_owner_digest_prefs(owner)
            await message.answer(
                _DIGEST_SECTIONS_HEADER_RU, reply_markup=_build_digest_sections_kb(prefs)
            )
            _log.info(
                "tg.command.digest_sections.shown",
                owner_telegram_id=owner,
                trackers_enabled=prefs.trackers_enabled,
                wiki_enabled=prefs.wiki_enabled,
            )
        except firing.DigestNotInitialisedError:
            await message.answer(_DIGEST_UNAVAILABLE_RU)
        except Exception:
            _log.exception("tg.command.digest_sections.error", owner_telegram_id=owner)
            await message.answer(_GENERIC_ERR_RU)

    @router.callback_query(F.data.startswith(DIGESTSEC_CALLBACK_PREFIX))
    async def _on_digestsec_callback(cb: CallbackQuery) -> None:
        owner = cb.from_user.id if cb.from_user else 0
        parsed = parse_digestsec_callback(cb.data or "")
        if parsed is None:
            _log.info(
                "tg.command.digest_sections.bad_callback", owner_telegram_id=owner, data=cb.data
            )
            await cb.answer(_DIGESTSEC_BAD_RU)
            return
        section, target = parsed
        try:
            prefs = await firing.set_owner_digest_section(owner, section=section, enabled=target)
            if cb.message is not None:
                await cb.message.edit_reply_markup(reply_markup=_build_digest_sections_kb(prefs))
            await cb.answer(_DIGESTSEC_DONE_RU)
            _log.info(
                "tg.command.digest_sections.toggled",
                owner_telegram_id=owner, section=section, enabled=target,
            )
        except Exception:
            _log.exception("tg.command.digest_sections.error", owner_telegram_id=owner)
            await cb.answer(_GENERIC_ERR_RU)
```
(`Message` and `CallbackQuery` types — import from `aiogram.types` if not already; the existing `/expand` handler and the confirm callback handler already use them, so the imports likely exist. `_GENERIC_ERR_RU` / `_DIGEST_UNAVAILABLE_RU` already exist in the file.)

3f. Header maintenance: `VERSION: 0.2.0`; `MODULE_CONTRACT` `SCOPE:` add `/digest_sections` + the `digestsec:` callback; `DEPENDS:` add `aiogram.types.InlineKeyboard*`, `ai_steward_wiki.storage.sessions.digest_prefs`; `LINKS:` add `ADR-026`, `aisw-pv8`; `MODULE_MAP` add `DIGESTSEC_CALLBACK_PREFIX`, `parse_digestsec_callback`; `__all__` add `"DIGESTSEC_CALLBACK_PREFIX"`, `"parse_digestsec_callback"`; prepend a `CHANGE_SUMMARY` entry; also amend the `(The text handler already excludes "/"-prefixed messages…)` note if needed (still true).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/tg -q && uv run mypy src/ai_steward_wiki/tg/handlers.py`
Expected: PASS; mypy clean.

- [ ] **Step 5: Run the router-order regression**

If `tests/unit/tg/test_handlers*.py` has a "plain text still reaches the pipeline" test, run it; if not, add a one-liner test that builds the router with the new handlers and dispatches a plain `F.text` message, asserting the pipeline's `on_text` was invoked. Run: `uv run pytest tests/unit/tg -q`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_steward_wiki/tg/handlers.py tests/unit/tg/test_digest_sections.py tests/unit/tg/
git commit -m "feat(M-TG-HANDLERS-WIRING): /digest_sections command + digestsec: toggle callback (aisw-pv8)"
```

---

### Task 6: Wire the sessions sessionmaker into `firing.set_digest_context` at startup

**Files:**
- Modify: `src/ai_steward_wiki/__main__.py`

- [ ] **Step 1: Locate the wiring**

Find the `firing.set_digest_context(...)` call in `__main__.py` (it currently passes `scheduler=`, `runner=`, `resolve_owner_wikis=`, `jobs_session_maker=`, `audit_session_maker=`, `sender=`). Find the sessions sessionmaker built at startup (the same one used for `users` / `pending_confirms` / `inbox_hint_cache` — search for where `storage.sessions.engine.build_sessionmaker` / the sessions DB URL is wired; it's likely named `sessions_maker` / `session_maker` / passed into the pipeline).

- [ ] **Step 2: Add the kwarg**

```python
    firing.set_digest_context(
        scheduler=scheduler,
        runner=digest_runner_adapter,
        resolve_owner_wikis=owner_wikis_resolver,
        jobs_session_maker=jobs_maker,
        audit_session_maker=audit_maker,
        sender=sender,
        sessions_session_maker=sessions_maker,   # <-- add this; use the actual variable name
    )
```

- [ ] **Step 3: Verify**

Run: `make lint` (ruff + ruff format --check + mypy + grace lint) and `uv run pytest tests/unit -q`.
Expected: all green. (If an integration/e2e test exercises startup wiring, run it too: `RUN_INTEGRATION=1 uv run pytest tests/integration -q -k digest` — optional, nightly.)

- [ ] **Step 4: Commit**

```bash
git add src/ai_steward_wiki/__main__.py
git commit -m "feat(M-RUNTIME-WIRING): pass sessions sessionmaker to digest context (aisw-pv8)"
```

---

### Task 7: `prompts/digest.md` — describe the «Не включай разделы» directive

**Files:**
- Modify: `prompts/digest.md`

- [ ] **Step 1: Edit the prompt**

Bump the first line `semver: 0.1.0` → `semver: 0.1.1`. After the paragraph that mentions the «Запланировано на ближайшие N ч …» block (the second paragraph), add a new sentence/line:

```
Если в сообщении есть строка вида «Не включай разделы: …» — полностью пропусти перечисленные секции, даже если по ним есть содержимое.
```

(Place it so it reads naturally — e.g. right after the «Запланировано…» sentence, before «Задача: …». Keep ru, no markdown tables, same tone.)

- [ ] **Step 2: Check for a prompt-version test**

If `tests/` asserts on `prompts/digest.md`'s semver or content hash (search for `digest.md` in tests), update the expected value. Run: `uv run pytest tests/unit -q -k "prompt or digest"`. Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add prompts/digest.md tests/
git commit -m "feat(prompts): digest.md 0.1.1 — honour the «Не включай разделы» directive (aisw-pv8)"
```

---

### Task 8: GRACE refresh + ADR-026 + changelog + report

**Files:**
- Create: `docs/adr/ADR-026-digest-section-toggles.md`
- Modify: `docs/knowledge-graph.xml`, `docs/verification-plan.xml`, `docs/development-plan.xml` (via `grace-refresh`)
- Modify: `docs/20260408_changelog.md`
- Create (if major): `docs/reports/20260512-inbox-wiki-digest-section-toggles-report.md`

- [ ] **Step 1: GRACE refresh**

Run `grace-refresh` (full) — picks up the new modules (`storage/sessions/digest_prefs.py`), the new model, the updated MODULE_CONTRACTs (`M-STORAGE-SESSIONS`, `M-SCHEDULER-FIRING`, `M-TG-HANDLERS-WIRING`, `M-RUNTIME-WIRING`), the `prompts/digest.md` semver bump, and the new log anchors (`tg.command.digest_sections.shown/.toggled/.bad_callback`, `scheduler.digest.sections_filtered`). Then `grace lint --failOn errors` → exit 0. If `prompts/digest_expand.md` / `prompts/digest.md` are graph nodes, confirm the semver bump propagated.

- [ ] **Step 2: Write ADR-026**

`docs/adr/ADR-026-digest-section-toggles.md` (`status: accepted`, `date: 2026-05-12`), modelled on ADR-025. Record the five Q&A decisions (TD-1…7) with rationale, AND the migration convention (TD-2): *"a migration that only adds tables → add the ORM model to `models.py` + a new `NNNN_*` whose `upgrade()` calls `Base.metadata.create_all` (idempotent) and whose `downgrade()` explicitly drops the added table(s); column ALTERs use explicit `op.*` batch ops"*. Link D-024, ADR-024, ADR-025, bd `aisw-pv8`, the discovery/design/plan docs. Note what stays out of scope (scheduling-time selection, per-WIKI toggles, `meds` toggle, cards — still deferred per ADR-025 §8).

- [ ] **Step 3: Changelog + report**

Add an entry to `docs/20260408_changelog.md` under the current date (feature: per-user digest section toggles, `/digest_sections`, `user_digest_prefs`, `alembic/sessions/0002`, bd `aisw-pv8`, ADR-026). Write `docs/reports/20260512-inbox-wiki-digest-section-toggles-report.md` via the `_report` skill (review + test results + Sentrux n/a — no `.sentrux/rules.toml`).

- [ ] **Step 4: Commit**

```bash
git add docs/adr/ADR-026-digest-section-toggles.md docs/knowledge-graph.xml docs/verification-plan.xml docs/development-plan.xml docs/20260408_changelog.md docs/reports/20260512-inbox-wiki-digest-section-toggles-report.md
git commit -m "docs(adr): ADR-026 digest section toggles + GRACE refresh + changelog + report (aisw-pv8)"
```

---

### Task 9: Full quality gate + close `aisw-pv8`

- [ ] **Step 1: Run the full gate**

Run, in order:
- `make lint` — ruff check, ruff format --check, mypy src, grace lint. Expected: all green.
- `uv run pytest tests/unit -q` — Expected: PASS, coverage of new modules ≥ 80% (check `--cov=ai_steward_wiki.storage.sessions.digest_prefs --cov-report=term-missing` if in doubt).
- `git status` — clean.
If anything fails, fix the root cause (no `--no-verify`, no skips) and re-run.

- [ ] **Step 2: Review pass**

`grace-reviewer` (full-integrity) + the `superpowers:code-reviewer` agent against the design doc + `superpowers:verification-before-completion` (paste the actual `pytest`/`make lint` output). Address findings.

- [ ] **Step 3: Close the bead**

```bash
bd update aisw-pv8 --notes="Done — user_digest_prefs + alembic/sessions/0002 (idempotent create_all delta) + /digest_sections inline-toggle command + fire_digest_job directive injection (byte-identical default) + prompts/digest.md 0.1.1 + ADR-026. Tests green, make lint green, grace lint exit 0."
bd dolt pull
bd close aisw-pv8 --reason="Phase-D.b.2c complete: per-user digest section toggles shipped end-to-end"
```
Then `bd dolt push` (session-close persistence).

---

## Self-review

- **Spec coverage:** FR-1 → Task 1 (model + repo) + Task 2 (migration). FR-2 → Task 2. FR-3 → Task 5 (`/digest_sections` + `digestsec:`) + Task 3 (firing accessors) + Task 6 (wiring). FR-4 → Task 4 (`fire_digest_job` directive) + Task 7 (`prompts/digest.md`). FR-5 → no `/expand` change (covered: `run_section_expand` explicitly untouched in Task 3/4; called out in Task 4 Step 3 and the design TD-6). FR-6 → Task 8 (ADR-026 + grace-refresh). NFR-1…8 → no new dep (verified Task 9 `make lint`), one table + one migration (Task 2), pragmas via existing listener (Task 1 — `Base` already wires `apply_sqlite_pragmas` in `engine.py`), UTC naive (`_now()` in `digest_prefs.py`), ru-only (Task 5 strings), mypy --strict (Task 9), structlog anchors (Tasks 4, 5; registered Task 8), degrade-to-all-on (Task 4 Step 3 try/except), TDD ≥80% (per-task RED→GREEN; Task 9 coverage check).
- **Placeholder scan:** the handler-level tests in Task 5 Step 1 and the `fire_digest_job` tests in Task 4 Step 1 reference "the existing aiogram test harness / digest e2e harness" rather than reproducing it — this is deliberate (the harness already exists in `tests/unit/tg/conftest.py` / `test_digest_e2e.py`; reproducing it would be wrong, not helpful). All *new* code is shown in full.
- **Type consistency:** `DigestPrefs(trackers_enabled, wiki_enabled)` + `.disabled_keys` used identically in Tasks 1, 3, 4, 5. `TOGGLEABLE_DIGEST_SECTIONS` / `SECTION_DISPLAY_NAME` defined once in `digest_prefs.py` (Task 1), imported in firing (Task 3/4) and handlers (Task 5). `parse_digestsec_callback(data) -> tuple[str, bool] | None` and `DIGESTSEC_CALLBACK_PREFIX = "digestsec:"` consistent across Task 5. `set_digest_context(..., sessions_session_maker=...)` 7-tuple consistent across Task 3 and Task 6. `firing.get_owner_digest_prefs` / `firing.set_owner_digest_section` names consistent across Tasks 3 and 5.
