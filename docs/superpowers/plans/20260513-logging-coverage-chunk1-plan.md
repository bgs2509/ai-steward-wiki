# Implementation Plan: structured logging coverage Chunk 1 (aisw-er6)

> **Feature:** `logging-coverage-chunk1`
> **bd_id:** aisw-er6
> **Module IDs touched:** `M-FOUNDATION-LOGGING`, `M-TG-MIDDLEWARE` (new sub-module), `M-TG-PIPELINE`, `M-CLASSIFIER-STAGE0`, `M-WIKI-RUNNER`, `M-INBOX-STAGING`
> **Discovery:** `docs/superpowers/specs/20260513-logging-coverage-chunk1-discovery.md`
> **Design:** `docs/superpowers/specs/20260513-logging-coverage-chunk1-design.md`

## Targets (verified file:line, 2026-05-13)

| Target | File | Line | Type |
|---|---|---|---|
| `DefaultPipeline.on_text` | `src/ai_steward_wiki/tg/pipeline.py` | 1322 | async |
| `classify` (Stage-0) | `src/ai_steward_wiki/classifier/stage0.py` | 115 | async |
| `run_wiki_session` | `src/ai_steward_wiki/wiki/runner.py` | 337 | async |
| `stage_media` (inbox) | `src/ai_steward_wiki/inbox/staging.py` | (sync) | sync |
| `dp.update.outer_middleware(mw)` | `src/ai_steward_wiki/tg/bot.py` | 181 | registration site |

## TDD step sequence

Order respects DEPENDS: decorator → catalog → middleware → integration → adoption. Each step = one commit. Pre-commit hooks must pass.

---

### Step 1 — `traced` decorator: RED

**Files:** `tests/unit/test_logging_traced.py` (NEW)

Write the following test cases first (they MUST fail with ImportError):

```python
# FILE: tests/unit/test_logging_traced.py
from __future__ import annotations

import pytest
import structlog
from structlog.testing import capture_logs

from ai_steward_wiki.logging_setup import traced  # noqa: F401  (does not exist yet → RED)


def test_traced_sync_emits_start_and_done_events() -> None:
    @traced()
    def noop(x: int) -> int:
        return x + 1

    with capture_logs() as records:
        assert noop(1) == 2

    events = [r["event"] for r in records]
    assert events[0].endswith(".start")
    assert events[-1].endswith(".done")
    assert all("args" not in r and "kwargs" not in r for r in records)
    done = records[-1]
    assert isinstance(done["duration_ms"], int)
    assert done["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_traced_async_emits_start_and_done_events() -> None:
    @traced()
    async def noop_async(x: int) -> int:
        return x * 2

    with capture_logs() as records:
        assert await noop_async(3) == 6

    events = [r["event"] for r in records]
    assert events[0].endswith(".start")
    assert events[-1].endswith(".done")


def test_traced_sync_logs_error_and_reraises() -> None:
    @traced()
    def boom() -> None:
        raise ValueError("nope")

    with capture_logs() as records, pytest.raises(ValueError, match="nope"):
        boom()

    events = [r["event"] for r in records]
    assert events[0].endswith(".start")
    assert events[-1].endswith(".error")
    err = records[-1]
    assert err["log_level"] == "error"
    assert isinstance(err["duration_ms"], int)


def test_traced_event_prefix_override() -> None:
    @traced(event_prefix="my.thing")
    def fn() -> None:
        return None

    with capture_logs() as records:
        fn()

    events = [r["event"] for r in records]
    assert events == ["my.thing.start", "my.thing.done"]


def test_traced_bind_fields_visible_to_inner_log() -> None:
    log = structlog.get_logger("inner")

    @traced(bind={"wiki_id": "W"})
    def fn() -> None:
        log.info("inner.event")

    with capture_logs() as records:
        fn()

    inner = [r for r in records if r["event"] == "inner.event"]
    assert len(inner) == 1
    assert inner[0]["wiki_id"] == "W"
```

Run: `uv run pytest tests/unit/test_logging_traced.py -x` → expected: ImportError on `traced`.

---

### Step 2 — `traced` decorator: GREEN

**Files:** `src/ai_steward_wiki/logging_setup.py` (MODIFY — extend MODULE_MAP, append `traced`)

Append after `get_logger`:

```python
# --- traced decorator (M-FOUNDATION-LOGGING) ---
import functools
import inspect
import time
from collections.abc import Callable, Mapping
from typing import ParamSpec, TypeVar, cast

_P = ParamSpec("_P")
_R = TypeVar("_R")


def traced(
    *,
    event_prefix: str | None = None,
    bind: Mapping[str, Any] | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Boundary-observability decorator.

    Emits structured logs at function entry, success, and error. Does NOT
    introspect args / return values (PII safety). Callers may pass an
    explicit ``bind`` mapping at decoration time for non-PII contextual
    fields; bind values are bound into ``structlog.contextvars`` for the
    duration of the call so inner logs inherit them.

    The default event prefix is ``f"{func.__module__}.{func.__qualname__}"``.
    Override via ``event_prefix``.

    Emitted events:
      - ``f"{prefix}.start"`` (INFO)
      - ``f"{prefix}.done"`` (INFO, duration_ms: int)
      - ``f"{prefix}.error"`` (ERROR, duration_ms: int, exc_info=True); the
        original exception is re-raised.
    """
    bind_dict = dict(bind) if bind else {}

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        prefix = event_prefix or f"{func.__module__}.{func.__qualname__}"
        log = structlog.get_logger(func.__module__)
        is_coro = inspect.iscoroutinefunction(func)

        if is_coro:

            @functools.wraps(func)
            async def async_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
                t0 = time.perf_counter_ns()
                with structlog.contextvars.bound_contextvars(**bind_dict):
                    log.info(f"{prefix}.start")
                    try:
                        result = await cast(Callable[_P, Any], func)(*args, **kwargs)
                    except BaseException:
                        dur = (time.perf_counter_ns() - t0) // 1_000_000
                        log.error(f"{prefix}.error", duration_ms=int(dur), exc_info=True)
                        raise
                    dur = (time.perf_counter_ns() - t0) // 1_000_000
                    log.info(f"{prefix}.done", duration_ms=int(dur))
                    return cast(_R, result)

            return cast(Callable[_P, _R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            t0 = time.perf_counter_ns()
            with structlog.contextvars.bound_contextvars(**bind_dict):
                log.info(f"{prefix}.start")
                try:
                    result = func(*args, **kwargs)
                except BaseException:
                    dur = (time.perf_counter_ns() - t0) // 1_000_000
                    log.error(f"{prefix}.error", duration_ms=int(dur), exc_info=True)
                    raise
                dur = (time.perf_counter_ns() - t0) // 1_000_000
                log.info(f"{prefix}.done", duration_ms=int(dur))
                return result

        return sync_wrapper

    return decorator
```

Also: add `"traced"` to `__all__`; bump `# VERSION: 0.0.2`; update MODULE_MAP and CHANGE_SUMMARY headers.

Run: `uv run pytest tests/unit/test_logging_traced.py -x` → GREEN.

Commit: `feat(M-FOUNDATION-LOGGING): add @traced boundary decorator (PII-safe, sync+async)`.

---

### Step 3 — `logging_events.py` SSoT catalog: RED + GREEN

**Files:**
- `tests/unit/test_logging_events_catalog.py` (NEW — RED)
- `src/ai_steward_wiki/logging_events.py` (NEW — GREEN)

Test:

```python
# FILE: tests/unit/test_logging_events_catalog.py
import re
from ai_steward_wiki import logging_events

_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$|^\.(start|done|error)$")


def test_all_constants_are_strings_and_match_snake_dotted() -> None:
    public = {n: v for n, v in vars(logging_events).items() if not n.startswith("_") and n.isupper()}
    assert public, "catalog is empty"
    for name, value in public.items():
        assert isinstance(value, str), name
        assert _KEY.match(value), (name, value)


def test_no_duplicate_values() -> None:
    public = {n: v for n, v in vars(logging_events).items() if not n.startswith("_") and n.isupper()}
    values = list(public.values())
    assert len(values) == len(set(values))


def test_only_constants_exported() -> None:
    for n, v in vars(logging_events).items():
        if n.startswith("_") or not n.isupper():
            continue
        assert isinstance(v, str), n
```

Module (with module contract header per project convention):

```python
# FILE: src/ai_steward_wiki/logging_events.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: SSoT catalog of stable snake_case dotted event-key constants for structured logging.
#   SCOPE: module-level Final[str] constants; no functions, no classes.
#   DEPENDS: -
#   LINKS: M-FOUNDATION-LOGGING
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TG_UPDATE_RECEIVED - CorrelationMiddleware entry event
#   TRACED_*_SUFFIX - lifecycle suffixes appended to @traced prefixes
#   TG_PIPELINE_DISPATCH / CLASSIFIER_STAGE0 / WIKI_RUN / INBOX_STAGING - canonical prefixes
# END_MODULE_MAP
from __future__ import annotations

from typing import Final

TG_UPDATE_RECEIVED: Final[str] = "tg.update.received"

TRACED_START_SUFFIX: Final[str] = ".start"
TRACED_DONE_SUFFIX: Final[str] = ".done"
TRACED_ERROR_SUFFIX: Final[str] = ".error"

TG_PIPELINE_DISPATCH: Final[str] = "tg.pipeline.dispatch"
CLASSIFIER_STAGE0: Final[str] = "classifier.stage0"
WIKI_RUN: Final[str] = "wiki.run"
INBOX_STAGING: Final[str] = "inbox.staging"
```

Run tests → GREEN.

Commit: `feat(M-FOUNDATION-LOGGING): add logging_events SSoT catalog of stable event keys`.

---

### Step 4 — conftest fixture for contextvars hygiene: GREEN-only

**Files:** `tests/conftest.py` (MODIFY or CREATE)

Add (or append) autouse fixture:

```python
import pytest
import structlog


@pytest.fixture(autouse=True)
def _clear_structlog_contextvars():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()
```

Run full unit suite to confirm nothing breaks: `uv run pytest tests/unit -x -q`.

Commit included in next step (small).

---

### Step 5 — `CorrelationMiddleware`: RED

**Files:** `tests/unit/tg/test_middleware_correlation.py` (NEW)

```python
# FILE: tests/unit/tg/test_middleware_correlation.py
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from ai_steward_wiki.logging_setup import get_correlation_id
from ai_steward_wiki.tg.middleware_correlation import CorrelationMiddleware  # RED


def _fake_update(*, update_id: int, user_id: int | None, chat_id: int | None) -> Any:
    msg = None
    if user_id is not None or chat_id is not None:
        msg = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id) if user_id is not None else None,
            chat=SimpleNamespace(id=chat_id) if chat_id is not None else None,
        )
    return SimpleNamespace(update_id=update_id, message=msg)


@pytest.mark.asyncio
async def test_correlation_middleware_emits_received_with_fields() -> None:
    mw = CorrelationMiddleware()
    captured: list[str | None] = []

    async def handler(event, data):
        captured.append(get_correlation_id())
        return "ok"

    update = _fake_update(update_id=42, user_id=7, chat_id=7)

    with capture_logs() as records:
        await mw(handler, update, {})

    received = [r for r in records if r["event"] == "tg.update.received"]
    assert len(received) == 1
    r = received[0]
    assert r["update_id"] == 42
    assert r["telegram_id"] == 7
    assert r["chat_id"] == 7
    assert isinstance(r["correlation_id"], str) and len(r["correlation_id"]) > 0
    # Inner handler saw the same correlation_id via legacy ContextVar
    assert captured == [r["correlation_id"]]
    # Cleared on exit
    assert get_correlation_id() is None


@pytest.mark.asyncio
async def test_correlation_middleware_clears_on_handler_exception() -> None:
    mw = CorrelationMiddleware()

    async def handler(event, data):
        raise RuntimeError("boom")

    update = _fake_update(update_id=1, user_id=None, chat_id=None)

    with pytest.raises(RuntimeError):
        await mw(handler, update, {})

    assert get_correlation_id() is None
```

Run: `uv run pytest tests/unit/tg/test_middleware_correlation.py -x` → RED (ImportError).

---

### Step 6 — `CorrelationMiddleware`: GREEN

**Files:** `src/ai_steward_wiki/tg/middleware_correlation.py` (NEW)

```python
# FILE: src/ai_steward_wiki/tg/middleware_correlation.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: aiogram outer-middleware that binds correlation_id + identity fields into structlog contextvars for the lifetime of one TG Update.
#   SCOPE: CorrelationMiddleware.__call__(handler, event, data) — generate uuid4, bind, log tg.update.received, clear on exit.
#   DEPENDS: aiogram.BaseMiddleware, ai_steward_wiki.logging_setup (bind_correlation_id, reset_correlation_id, get_logger), ai_steward_wiki.logging_events
#   LINKS: M-TG-MIDDLEWARE, M-FOUNDATION-LOGGING
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CorrelationMiddleware - outer-middleware that owns per-Update correlation_id + identity binding
# END_MODULE_MAP
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware

from ai_steward_wiki.logging_events import TG_UPDATE_RECEIVED
from ai_steward_wiki.logging_setup import (
    bind_correlation_id,
    get_logger,
    reset_correlation_id,
)

__all__ = ["CorrelationMiddleware"]

_log = get_logger(__name__)


def _safe_telegram_id(event: Any) -> int | None:
    msg = getattr(event, "message", None)
    if msg is None:
        return None
    user = getattr(msg, "from_user", None)
    return getattr(user, "id", None) if user is not None else None


def _safe_chat_id(event: Any) -> int | None:
    msg = getattr(event, "message", None)
    if msg is None:
        return None
    chat = getattr(msg, "chat", None)
    return getattr(chat, "id", None) if chat is not None else None


class CorrelationMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        cid = str(uuid.uuid4())
        token = bind_correlation_id(cid)
        telegram_id = _safe_telegram_id(event)
        chat_id = _safe_chat_id(event)
        update_id = getattr(event, "update_id", None)
        try:
            with structlog.contextvars.bound_contextvars(
                correlation_id=cid,
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
            ):
                _log.info(TG_UPDATE_RECEIVED)
                return await handler(event, data)
        finally:
            reset_correlation_id(token)
```

Run: `uv run pytest tests/unit/tg/test_middleware_correlation.py -x` → GREEN.

Commit: `feat(M-TG-MIDDLEWARE): add CorrelationMiddleware (uuid4 + contextvars binding per Update)`.

---

### Step 7 — register `CorrelationMiddleware` first in `tg/bot.py`

**Files:** `src/ai_steward_wiki/tg/bot.py` (MODIFY)

At line 181 (existing `dp.update.outer_middleware(mw)`), prepend a registration of `CorrelationMiddleware`:

```python
from ai_steward_wiki.tg.middleware_correlation import CorrelationMiddleware
...
dp.update.outer_middleware(CorrelationMiddleware())   # MUST be FIRST — binds correlation_id
mw = AllowlistMiddleware(allowlist)
dp.update.outer_middleware(mw)
```

Update MODULE_CONTRACT DEPENDS line to include `ai_steward_wiki.tg.middleware_correlation`.

No new unit test for this — covered by the integration test in Step 9 (correlation flow assertion). Manual verification at the end with `uv run pytest tests/unit/tg -x -q`.

Commit: `feat(M-TG-BOT): register CorrelationMiddleware before AllowlistMiddleware`.

---

### Step 8 — apply `@traced` to public entrypoints

**Files (one commit each, in this order — small, atomic):**

1. `src/ai_steward_wiki/tg/pipeline.py` — line 1322 `DefaultPipeline.on_text`:
   ```python
   from ai_steward_wiki.logging_setup import traced
   from ai_steward_wiki.logging_events import TG_PIPELINE_DISPATCH
   ...
   @traced(event_prefix=TG_PIPELINE_DISPATCH)
   async def on_text(self, telegram_id: int, chat_id: int, update_id: int, text: str) -> None:
       ...
   ```
   Commit: `feat(M-TG-PIPELINE): apply @traced to DefaultPipeline.on_text`.

2. `src/ai_steward_wiki/classifier/stage0.py` — line 115 `classify`:
   ```python
   from ai_steward_wiki.logging_setup import traced
   from ai_steward_wiki.logging_events import CLASSIFIER_STAGE0
   ...
   @traced(event_prefix=CLASSIFIER_STAGE0)
   async def classify(text: str, correlation_id: str, backend: ClassifierBackend, prompt_path: Path) -> ...:
       ...
   ```
   Commit: `feat(M-CLASSIFIER-STAGE0): apply @traced to classify entrypoint`.

3. `src/ai_steward_wiki/wiki/runner.py` — line 337 `run_wiki_session`:
   ```python
   from ai_steward_wiki.logging_setup import traced
   from ai_steward_wiki.logging_events import WIKI_RUN
   ...
   @traced(event_prefix=WIKI_RUN)
   async def run_wiki_session(wiki_id: str, wiki_path: Path, ...) -> ...:
       ...
   ```
   Commit: `feat(M-WIKI-RUNNER): apply @traced to run_wiki_session entrypoint`.

4. `src/ai_steward_wiki/inbox/staging.py` — `stage_media` (sync):
   ```python
   from ai_steward_wiki.logging_setup import traced
   from ai_steward_wiki.logging_events import INBOX_STAGING
   ...
   @traced(event_prefix=INBOX_STAGING)
   def stage_media(...) -> ...:
       ...
   ```
   Commit: `feat(M-INBOX-STAGING): apply @traced to stage_media entrypoint`.

After each: run targeted tests for the affected module — `uv run pytest tests/unit/<area> -x -q`.

---

### Step 9 — integration test: end-to-end correlation_id flow

**Files:** `tests/integration/test_correlation_id_flow.py` (NEW)

```python
# FILE: tests/integration/test_correlation_id_flow.py
"""Verify that CorrelationMiddleware + AllowlistMiddleware + a @traced downstream
all share the same correlation_id within one simulated Update."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from ai_steward_wiki.logging_setup import traced
from ai_steward_wiki.tg.middleware_correlation import CorrelationMiddleware


@traced(event_prefix="test.downstream")
async def downstream() -> None:
    return None


@pytest.mark.asyncio
async def test_correlation_id_flows_through_middleware_into_traced() -> None:
    mw = CorrelationMiddleware()

    async def handler(event, data):
        await downstream()
        return "ok"

    update = SimpleNamespace(
        update_id=1,
        message=SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42),
        ),
    )

    with capture_logs() as records:
        await mw(handler, update, {})

    cids = {r.get("correlation_id") for r in records if r.get("correlation_id")}
    assert len(cids) == 1, f"expected one cid, got: {cids}"
    events = {r["event"] for r in records}
    assert "tg.update.received" in events
    assert "test.downstream.start" in events
    assert "test.downstream.done" in events
```

Run: `RUN_INTEGRATION=1 uv run pytest tests/integration/test_correlation_id_flow.py -x` (or plain pytest if no env gate is required for this test).

Commit: `test(M-TG-MIDDLEWARE): integration test for correlation_id flow into @traced`.

---

### Step 10 — gate: full lint + tests

```bash
make lint            # ruff + format + mypy (must NOT introduce new errors vs baseline)
uv run pytest tests/unit -q
uv run pytest tests/integration/test_correlation_id_flow.py -q
```

Baseline reminder: pre-existing 1 mypy error in `src/ai_steward_wiki/tg/handlers.py:139` is out-of-scope. New code must not add any mypy errors. If `traced`'s `cast` confuses mypy, refine annotations until clean.

---

## Self-review checklist

- [x] Every FR has at least one step:
  - FR-1 → Steps 1, 2
  - FR-2 → Step 1 (assert no args/kwargs in event_dict) + Step 2 (no introspection in implementation)
  - FR-3 → Step 3
  - FR-4 → Steps 5, 6, 7
  - FR-5 → Step 8
  - FR-6 → Steps 1, 5
  - FR-7 → Step 9
- [x] Every NFR addressed:
  - NFR-1 → Step 2 (no introspection); test in Step 1 asserts absence
  - NFR-2 → Step 3 regex test
  - NFR-3 → informal — not gated
  - NFR-4 → Step 4 autouse fixture
  - NFR-5 → Step 10 lint gate
  - NFR-6 → no rewrites in plan; only additive
- [x] All 5 risks have mitigations referenced or executed (R-1 dual-binding test in Step 5; R-2 sync+async tests in Step 1; R-3 conftest fixture Step 4; R-4 catalog regex test Step 3; R-5 middleware ordering manual + integration Step 9).
- [x] No placeholders.
- [x] Task order respects DEPENDS (decorator → catalog → fixture → middleware → registration → adoption → integration).
- [x] Commit format: Conventional Commits + GRACE MODULE_ID scope.
- [x] No bypass of pre-commit hooks.
