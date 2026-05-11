---
feature: M-TG-TEXT
bd_id: aisw-187
status: stable
date: 2026-05-11
chunk: 10
---

# Design — Chunk 10 / M-TG-TEXT

## Module layout

```
src/ai_steward_wiki/tg/
├── __init__.py            # BARREL, MODULE_CONTRACT, re-exports
├── bot.py                 # build_dispatcher, build_bot, TG types Protocol seam
├── middleware_auth.py     # AllowlistMiddleware (aiogram 3 BaseMiddleware)
├── confirm.py             # ConfirmationService (auto/implicit/explicit + TTL)
├── output.py              # deliver_output, HtmlBalancer, ChainSplitter, run_outputs persistence
└── stream_edit.py         # StreamEditor (throttle, chain-split, final-flush)
```

## Type seams (Protocols)

To keep unit tests fast and offline, all aiogram side-effects pass through
narrow Protocols implemented by aiogram's `Bot` in production and by Fakes
in tests:

```python
class TgSender(Protocol):
    async def send_message(self, chat_id: int, text: str, *,
                            parse_mode: str | None = ...,
                            reply_markup: object | None = ...) -> SentMessage: ...
    async def edit_message_text(self, chat_id: int, message_id: int, text: str,
                                 *, parse_mode: str | None = ...,
                                 reply_markup: object | None = ...) -> None: ...
    async def send_document(self, chat_id: int, *, path: Path, caption: str | None = ...) -> SentMessage: ...

class SentMessage(Protocol):
    message_id: int
```

`aiogram.Bot` satisfies `TgSender` structurally via duck-typing in production
(wrapped by a thin adapter `AiogramSender` in `bot.py`).

## D-023 — graduated confirmation

Three-level enum `ConfirmLevel = auto | implicit | explicit`. Caller chooses
level per category.

- `auto`: `ConfirmationService.auto_ack(chat_id, line)` — single send.
- `implicit`: `implicit_ack(chat_id, recap, *, keyboard=None)` — non-blocking
  recap; returns immediately; click events are advisory.
- `explicit`: `request_explicit(...)` — persists `PendingConfirm` row, sends
  recap + 3-button keyboard, schedules TTL via APScheduler in caller (not in
  scope here — confirm service exposes `expire_due(now_utc)` so the existing
  scheduler can call it).

Storage adapter writes the *minimal* fields supported by baseline
`PendingConfirm` (telegram_id, payload_hash, expires_at_utc, created_at_utc)
plus optional extension columns introduced by Alembic revision
`0002_pending_confirms_d023` (status, category, chat_id, recap_message_id,
draft_json). Migration is **additive** — existing rows survive.

`payload_hash` = `sha256(canonical_json(draft))[:64]`. Idempotency: an
existing pending with same (telegram_id, payload_hash) in `pending` state is
returned instead of creating a duplicate.

Resolution: `resolve(telegram_id, pending_id, action)` updates status under
`WHERE status='pending'` (race-safe). `expire_due` flips stale rows to
`expired`.

## D-025 — output size hybrid

```python
async def deliver_output(
    *, sender: TgSender,
    chat_id: int,
    telegram_id: int,
    wiki_id: str,
    run_id: str,
    text: str,
    runs_dir: Path,
    audit_session_maker: async_sessionmaker[AsyncSession],
    job_id: int | None = None,
    summarizer: HaikuSummarizer | None = None,
) -> DeliveryReceipt
```

Steps:
1. **Persist** full text to `<runs_dir>/<YYYY-MM-DD>/<run_id>.md` (atomic
   write via tmp + `os.replace`) with YAML frontmatter (`run_id, wiki_id,
   chat_id, ts, size, sha256`).
2. **Branch by size**:
   - `len(text) ≤ 3500` → single `send_message`.
   - `≤ 10000` → `ChainSplitter.split(text, max_part=3500, hard_cap=3)`
     yields HTML-balanced segments with `(i/M)` suffix.
   - `> 10000` → `summary = await summarizer.summarize(text)` (Russian, ≤1500
     chars); send summary; `send_document(path)` for the on-disk file.
3. **Record** in `audit.run_outputs` with `kind ∈ {reply, digest, ingest_report}`
   (caller passes `kind`). Returns `DeliveryReceipt(run_id, kind, n_messages,
   summary_chars, output_path, output_bytes, output_sha256)`.

`HaikuSummarizer` is a Protocol; tests inject `StubSummarizer`. Real
implementation lives in classifier (Stage-0 Haiku); a thin adapter will be
wired in chunk 12. For chunk 10 we ship the Protocol + `LengthCapSummarizer`
fallback (truncates to 1500 chars + `…`) when no real summarizer wired —
ensures the `>10000` branch is fully functional without depending on chunk-12.

### HtmlBalancer

Tokeniser splits the text into `(tag, text)` events. A stack of open tags is
maintained; supported tags = `{b, i, u, s, a, code, pre}` (D-024 whitelist).
On segment boundary:
- emit `</tag>` for each tag in reverse-stack order;
- on next-segment start, re-emit `<tag …attrs…>` in original order.

Special chars `<`, `>`, `&` outside tags are HTML-escaped *upstream* by
producers (per D-026 §"HTML safety") — balancer does not re-escape. The
balancer's job is exclusively tag-pair integrity.

### ChainSplitter

Greedy fill up to `max_part_chars` then walk back to nearest semantic
boundary (priority `<b>` header → blank line → `. \n` → `. `). Appends
`(i/M)` footer outside HTML tags. Hard cap `3` parts per D-025; if more
needed, caller falls through to document path (>10000 already triggers it).

## D-026 — streaming edits

`StreamEditor` encapsulates per-run edit state:

```python
class StreamEditor:
    def __init__(self, *, sender, chat_id, first_message_id, *,
                 tick_s=1.5, delta_chars=50, chain_threshold=4000): ...
    async def feed(self, chunk: str) -> None: ...
    async def finalize(self) -> None: ...  # always emits final state
```

Internally:
- a single background task runs an `await asyncio.sleep(remaining)` loop and
  edits when either condition fires.
- `feed` simply appends to buffer + sets a `_dirty` flag and computes delta.
- On buffer length nearing `chain_threshold` (default 4000), splitter
  finalizes current message (balanced tags + `(i)` footer + "продолжаю…"),
  sends new placeholder, switches edit target.
- `finalize()` is **idempotent**: cancels background task, performs one last
  edit with the canonical balanced final segment, removes `⏳`, appends
  `(N/N)`. Called in `try/finally` by the caller (run-driver). On exception
  the same final-flush guarantee applies — `finalize()` swallows non-fatal
  send errors and still records final state in the in-memory log so the
  on-disk persistence in `deliver_output` still happens.

Throttle clock is injected (`time.monotonic` by default) — tests use a
fake-clock implementation to assert exact tick behaviour.

## Data model deltas

Migration `alembic/sessions/versions/0002_pending_confirms_d023.py`:
```sql
ALTER TABLE pending_confirms ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE pending_confirms ADD COLUMN category TEXT;
ALTER TABLE pending_confirms ADD COLUMN chat_id INTEGER;
ALTER TABLE pending_confirms ADD COLUMN recap_message_id INTEGER;
ALTER TABLE pending_confirms ADD COLUMN draft_json TEXT;
```
ORM model gets matching `Mapped[…]` fields. `audit.run_outputs` already has
the columns we need (chunk 2 baseline).

## Decisions (3x-rule snapshot)

1. **Confirmation TTL store: sessions.db vs in-memory** — 3 candidates
   (sessions.db row / in-memory dict / Redis). Decision: **sessions.db row**.
   Rationale ≥3×: only sessions.db survives restart, supports TTL audit and
   matches D-023 storage sketch verbatim. In-memory loses state on restart;
   Redis violates 3-DB constraint.
2. **HTML balancer algorithm** — 3 candidates (stack-based tokeniser /
   regex-only / parse via stdlib html.parser). Decision: **stack-based
   tokeniser** (custom, ~80 LOC). Rationale: regex-only fails on nested
   tags; stdlib parser is permissive HTML5, drops unknown attrs. Stack with
   whitelisted tag set is the simplest 3×-dominant fit.
3. **Throttle implementation** — 3 candidates (background-task + sleep /
   APScheduler trigger / asyncio.Event with manual wait). Decision:
   **background-task + sleep**. APScheduler is heavy per-stream; Event
   doesn't give us tick interval. Chosen approach mirrors D-026 sample code.
4. **Summarizer adapter for >10000** — 3 candidates (real Haiku / cap-only
   fallback / hard error). Decision: **Protocol + `LengthCapSummarizer`
   default**, real adapter pluggable in chunk 12. Auto-picked under
   uncertainty — caller passes adapter; if not provided we degrade gracefully
   (consistent with D-025 spirit of "summary delivery best-effort").

## Verification

`tests/unit/tg/`:
- `test_middleware_auth.py` — allow / deny / log shape (≥3 cases).
- `test_confirm.py` — auto/implicit/explicit flows, TTL, idempotent
  duplicate, race resolve-vs-expire (≥7 cases).
- `test_output.py` — three size branches, balancer round-trips,
  chain-split footer markers, persistence to file + audit row (≥9 cases).
- `test_stream_edit.py` — throttle tick, delta trigger, chain-split,
  final-flush on normal end and on exception, HTML balance across boundary
  (≥7 cases).
- `test_bot.py` — dispatcher wires middleware before any handler (≥2 cases).

Total target: ≥28 new unit tests. Existing suite (160) must remain green.
