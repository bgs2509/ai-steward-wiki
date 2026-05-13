---
feature: logging-coverage-chunk1
bd_id: aisw-er6
module_id: M-FOUNDATION-LOGGING
status: draft
date: 2026-05-13
references_discovery: docs/superpowers/specs/20260513-logging-coverage-chunk1-discovery.md
stack:
  - structlog (existing; ≥ 24.x in pyproject)
  - aiogram 3.15 (existing)
  - python 3.11+ inspect.iscoroutinefunction, time.perf_counter_ns, contextlib
  - structlog.contextvars (bound_contextvars, clear_contextvars, bind_contextvars)
  - structlog.testing.capture_logs (existing tests pattern)
new_modules:
  - src/ai_steward_wiki/logging_events.py
  - src/ai_steward_wiki/tg/middleware_correlation.py
modified_modules:
  - src/ai_steward_wiki/logging_setup.py (add traced)
  - src/ai_steward_wiki/tg/bot.py (register middleware)
  - tests/conftest.py (autouse fixture)
applied_to_existing:
  - src/ai_steward_wiki/tg/pipeline.py (1–2 public entrypoints)
  - src/ai_steward_wiki/classifier/__init__.py or stage entrypoints
  - src/ai_steward_wiki/inbox/staging.py (1 entrypoint)
  - src/ai_steward_wiki/wiki/run.py or equivalent run entrypoint
adr_needed: false
---

# Design: structured logging coverage Chunk 1 (aisw-er6)

## 1. Approach

Three additive pieces, no rewrites of existing logs:

1. **`@traced` decorator** in `logging_setup.py` — single decorator handling both async and sync callables via runtime `inspect.iscoroutinefunction` dispatch. Emits three events keyed off `f"{module}.{qualname}.{start|done|error}"`. Optional `bind: Mapping[str, Any] | None` parameter at decoration time for caller-controlled non-PII fields. Optional `event_prefix: str | None` to override the auto-derived key when caller wants a stable namespace.
2. **`logging_events.py`** — module exposing a frozen, importable catalog of stable event-key constants. Three sections: `CORRELATION_*` (middleware events), `TRACED_*` (lifecycle suffixes), `TG_*` / `CLASSIFIER_*` / `INBOX_*` / `WIKI_*` (canonical names for the public entrypoints decorated in Chunk 1). Constants only — no functions.
3. **`CorrelationMiddleware`** in `tg/middleware_correlation.py` — `aiogram.BaseMiddleware` subclass. On every Update: generates `uuid4` correlation_id, calls both `logging_setup.bind_correlation_id(...)` (legacy ContextVar agreement, per R-1) AND `structlog.contextvars.bind_contextvars(correlation_id=..., telegram_id=..., chat_id=..., update_id=...)`. Uses `try/finally` to clear both on exit. Logs one INFO event `tg.update.received` on entry (this also serves as the smoke event for correlation flow tests).

Registered in `tg/bot.py` BEFORE `AllowlistMiddleware` so deny events inherit the binding.

## 2. `@traced` semantics (concrete)

```python
P = ParamSpec("P")
R = TypeVar("R")

def traced(
    *,
    event_prefix: str | None = None,
    bind: Mapping[str, Any] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    ...
```

Behaviour:

1. Derive `prefix = event_prefix or f"{func.__module__}.{func.__qualname__}"`.
2. On call:
   - `t0 = time.perf_counter_ns()`
   - context manager: `structlog.contextvars.bound_contextvars(**(bind or {}))`
   - `log.info(f"{prefix}.start")`
   - try: call → `log.info(f"{prefix}.done", duration_ms=int((perf_counter_ns()-t0)/1_000_000))` → return
   - except: `log.error(f"{prefix}.error", duration_ms=..., exc_info=True)`; re-raise
3. Sync vs async dispatch chosen ONCE at decoration time via `inspect.iscoroutinefunction(func)`; decorated wrapper preserves signature via `functools.wraps`.
4. **No `args` / `kwargs` / return value ever logged.** Caller-only `bind` for non-PII context.
5. `log = structlog.get_logger(func.__module__)` resolved at decoration time (cheap, structlog caches).

## 3. CorrelationMiddleware semantics

```python
class CorrelationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        cid = str(uuid.uuid4())
        token = bind_correlation_id(cid)  # legacy ContextVar
        update_id = getattr(event, "update_id", None)
        # Extract telegram_id, chat_id from event with safe getattr chains
        try:
            with structlog.contextvars.bound_contextvars(
                correlation_id=cid,
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
            ):
                _log.info("tg.update.received")
                return await handler(event, data)
        finally:
            reset_correlation_id(token)
```

Field extraction is best-effort with `getattr(event.message, "from_user", None) and event.message.from_user.id`. Missing fields become `None` (structlog drops `None` in JSON only if processor configured — here they ride explicitly).

## 4. logging_events.py SSoT catalog

Format:
```python
# FILE: src/ai_steward_wiki/logging_events.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: stable snake_case event-key constants for structured logging (SSoT catalog).
#   SCOPE: module-level Final[str] constants; no functions, no classes.
#   DEPENDS: -
#   LINKS: M-FOUNDATION-LOGGING (consumed by @traced default prefixes and middleware)
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
from typing import Final

# Middleware
TG_UPDATE_RECEIVED: Final[str] = "tg.update.received"

# @traced lifecycle suffixes (appended to prefix)
TRACED_START_SUFFIX: Final[str] = ".start"
TRACED_DONE_SUFFIX: Final[str] = ".done"
TRACED_ERROR_SUFFIX: Final[str] = ".error"

# Canonical prefixes for Chunk 1-decorated entrypoints
TG_PIPELINE_DISPATCH: Final[str] = "tg.pipeline.dispatch"
CLASSIFIER_STAGE0: Final[str] = "classifier.stage0"
CLASSIFIER_STAGE1: Final[str] = "classifier.stage1"
INBOX_STAGING: Final[str] = "inbox.staging"
WIKI_RUN: Final[str] = "wiki.run"
```

Names finalised after reading actual entrypoints in Execution; Discovery names are placeholders refined at Step 7.

## 5. Data flow (one TG update, end-to-end)

```
TG Update arrives
  └─> Dispatcher
        └─> CorrelationMiddleware
              ├─ cid = uuid4
              ├─ bind_correlation_id(cid)                     [legacy ContextVar]
              ├─ structlog.contextvars.bind_contextvars(
              │     correlation_id=cid, telegram_id, chat_id, update_id)
              ├─ _log.info("tg.update.received")              [first record w/ binding]
              └─> AllowlistMiddleware
                    ├─ logs "auth.deny" etc. WITH correlation_id (auto-merged)
                    └─> handler
                          └─> @traced public function
                                ├─ "<prefix>.start"            [w/ correlation_id]
                                ├─ ... work ...
                                └─ "<prefix>.done" duration_ms [w/ correlation_id]
```

R-1 (dual sources) verified by integration test asserting equality across CorrelationMiddleware record and a @traced record from the same Update.

## 6. Module boundaries and contracts (preview for Step 7)

| Module | Role | Public surface |
|---|---|---|
| `logging_setup.py` | RUNTIME | `configure_logging`, `bind_correlation_id`, `reset_correlation_id`, `get_correlation_id`, `get_logger`, **NEW** `traced` |
| `logging_events.py` | TYPES | Final[str] constants only |
| `tg/middleware_correlation.py` | RUNTIME | `CorrelationMiddleware` (BaseMiddleware) |
| `tg/bot.py` | RUNTIME (existing) | registration order updated |

## 7. Test plan (preview for Step 9)

1. **`tests/unit/test_logging_traced.py`**
   - decorates a sync no-op → asserts 2 records (start, done) with correct keys + duration_ms int ≥ 0; no `args` / `kwargs` keys in event_dict.
   - decorates an async no-op → same.
   - decorates a function that raises ValueError → asserts start + error records, duration_ms present, `exc_info` truthy in capture; exception re-raised to caller.
   - decorates with explicit `event_prefix="my.thing"` → records use `my.thing.start` / `my.thing.done`.
   - decorates with `bind={"wiki_id": "W"}` → `wiki_id` present in records emitted INSIDE the function (via separate get_logger().info call within decorated body).

2. **`tests/unit/test_logging_events_catalog.py`**
   - All exported constants are `str`, all match `^[a-z][a-z0-9_.]*$`, no duplicates.
   - `dir(logging_events)` exports only constants (no callables).

3. **`tests/unit/tg/test_middleware_correlation.py`**
   - construct fake Update mock → call middleware → assert `tg.update.received` emitted with correlation_id, telegram_id, chat_id, update_id fields; assert legacy ContextVar equal to bound value; assert binding cleared after exit.

4. **`tests/integration/test_correlation_id_flow.py`** (light)
   - simulate one Update through CorrelationMiddleware + AllowlistMiddleware + a downstream @traced function (no real TG, no real Claude); assert all records share same correlation_id.

5. **`tests/conftest.py`** — autouse fixture:
   ```python
   @pytest.fixture(autouse=True)
   def _clear_structlog_contextvars():
       structlog.contextvars.clear_contextvars()
       yield
       structlog.contextvars.clear_contextvars()
   ```

## 8. Risks and mitigations (deltas from Discovery)

All Discovery risks (R-1..R-5) accepted unchanged. No new design-level risks emerged.

## 9. Out of scope (reaffirmed)

Chunk 2 deferral list unchanged from Discovery §"Out of scope".

## 10. ADR

Not required. The two architectural choices (single decorator with runtime dispatch, dual-source correlation_id with mitigation R-1) are documented inline in this design and recoverable from the implementation. Promote to ADR only if Chunk 2 reveals a need to revisit.

Proceeding to GRACE Ask + Plan (auto-approved).
