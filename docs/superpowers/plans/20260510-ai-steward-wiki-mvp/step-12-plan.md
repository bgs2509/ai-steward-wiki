# step-12-plan.md — Chunk 12 / M-ONBOARD-ADMIN

**bd_id:** aisw-zsg
**Module:** M-ONBOARD-ADMIN
**Window estimate:** 0.45
**Sources:** §11 (Onboarding) + §12 (Admin) of `docs/Spec-WIKI/research/tech-spec-draft.md`; D-031 (allowlist + admin shadow channel — failures only, no content); D-032 (ru-only MVP); D-042 (identity vocabulary: `telegram_id` canonical). Depends on chunk 3 (M-AUTH-USERS, users.toml + SIGHUP hot-reload) and chunk 10 (M-TG-TEXT, aiogram dispatcher + AllowlistMiddleware + ConfirmationService).

## Goal

Wire onboarding for unknown Telegram users and an admin command surface:

1. `/start` from a `telegram_id` not in the allowlist → record in `sessions.pending_users` with 14-day TTL, emit a single bilingual-but-ru-only intro message + a hint that an admin must approve. Re-`/start` while pending is idempotent (refresh TTL, do not duplicate row).
2. Admin inline-approve flow — admin receives a notification (shadow channel, failures only — but here pending-user is a *signal*, NOT a failure; goes to the standard admin chat). Inline keyboard `[Approve] [Reject]`. On Approve → write to `users.toml` (atomic tmp+rename), trigger SIGHUP path from chunk 3, move row pending_users → users (via existing chunk 3 sync). On Reject → mark `status='rejected'`, audit, send polite ru-only refusal.
3. Mandatory onboarding intro template — `templates/onboarding-intro.ru.md` — 6 required slugged sections with explicit HTML markers used as anchors by the lint:
   - `<!-- slug:greeting -->` приветствие
   - `<!-- slug:purpose -->` зачем нужен бот
   - `<!-- slug:capabilities -->` что умеет (текст / фото / голос / напоминания)
   - `<!-- slug:privacy -->` приватность данных + 14-дневный TTL pending
   - `<!-- slug:next-steps -->` что сделать дальше (дождаться апрува)
   - `<!-- slug:contact -->` куда писать в случае проблем
4. `scripts/lint_onboarding.py` — exit 1 on missing slug, duplicate slug, slug order drift, or empty section. CI-gated via `make lint` (extend Makefile target).
5. `TENANCY_MODE` env (pydantic-settings) — `single` (default) | `multi`. In `single`: only the bootstrap admin from `users.toml` may approve; in `multi`: any user with `role=admin` flag may approve. Affects who receives pending notifications.
6. `/admin elevate` — promotes a session for 30 min by writing a row into `sessions.pending_confirms` with `category='admin_elevation'`, `expires_at=now+30min`. `/admin demote` removes it. Subsequent admin-only commands check the elevation row in the chunk-10 ConfirmationService path.
7. `audit.admin_events` — append-only row `(ts_utc, actor_tg_id, target_tg_id, action, outcome, reason)`. Actions: `pending_created`, `approve`, `reject`, `elevate`, `demote`, `elevation_expired`.
8. **Admin shadow channel** (D-031): failures (CLI crash, classifier escalation, DLQ insert) get forwarded to admin chat *without user-visible content* — only `correlation_id`, `failure_kind`, `wiki_id` metadata. This module ships the small forwarder `admin.shadow_emit(failure_event)`; callers wire it in later (chunks 13/14/16). No content leakage — enforced by structural typing of the payload.

## Steps (TDD)

1. **Recon** — read `auth/users_toml.py`, `auth/allowlist.py`, `auth/sighup.py` (chunk 3); `tg/bot.py`, `tg/middleware_auth.py`, `tg/confirm.py` (chunk 10); `storage/sessions/models.py` (chunk 2 — verify `pending_users`, `pending_confirms`, `users` tables exist; if `admin_events` is missing in `storage/audit/models.py`, add it additively via a new Alembic revision `005_admin_events.py`).
2. **Tests RED** under `tests/unit/auth/`:
   - `test_onboarding.py`
     - `start_unknown_user(telegram_id, chat_id, username) -> PendingUser` creates a row with `expires_at=now+14d`, returns existing row on repeat call (TTL refresh, no duplicate).
     - sweep job `purge_expired_pending(now)` deletes rows past TTL; emits `pending_expired` audit row.
     - `format_intro_message(template_path, locale='ru') -> str` substitutes `{bot_name}` placeholder; raises `OnboardingTemplateError` if a required slug is missing.
   - `test_admin.py`
     - `approve_pending(admin_tg_id, target_tg_id, users_toml_path) -> ApprovalResult` validates admin authority per `TENANCY_MODE`, writes users.toml atomically, sends SIGHUP, returns `(ok=True, user_added=True)`. Idempotent on re-approve.
     - `reject_pending(admin_tg_id, target_tg_id, reason) -> RejectionResult` marks `status='rejected'`, writes `admin_events`.
     - `elevate(admin_tg_id, ttl=timedelta(minutes=30)) -> ElevationToken` creates `pending_confirms` row; `is_elevated(admin_tg_id, now) -> bool` returns True only inside the window; `demote(admin_tg_id)` deletes it.
     - `assert_admin(admin_tg_id, tenancy='single')` raises `NotAnAdmin` for non-bootstrap admin; in `'multi'` accepts any `role=admin` from users.toml.
     - `shadow_emit(failure_event)` rejects events with non-empty `content` field at type level (Pydantic strict); allows `metadata`-only events.
   - `test_lint_onboarding.py`
     - happy path → exit 0
     - missing `<!-- slug:privacy -->` → exit 1, stderr names the slug
     - duplicate slug → exit 1
     - empty section → exit 1
3. **GREEN** — implement:
   - `src/ai_steward_wiki/auth/onboarding.py` — `PendingUserRepo` (CRUD on `sessions.pending_users`); `start_unknown_user`, `purge_expired_pending`, `format_intro_message` (Jinja-free, plain `str.format` over a slug-validated template; raises on missing slug); ru-only string constants.
   - `src/ai_steward_wiki/auth/admin.py` — `AdminService` with `approve_pending`, `reject_pending`, `elevate`, `demote`, `is_elevated`, `assert_admin`; `ShadowEmitter` Protocol + `LoggingShadowEmitter` default; `FailureEvent` Pydantic model with **no `content` field** (only `correlation_id`, `failure_kind`, `wiki_id`, `extra: dict[str, str]` for metadata-only).
   - `templates/onboarding-intro.ru.md` — 6 sections with HTML slug markers; copy reviewed for ru-only, polite, ≤2000 chars.
   - `scripts/lint_onboarding.py` — argparse `--template <path>`; reads required slug list from a constant; validates presence/order/uniqueness/non-empty.
   - `src/ai_steward_wiki/storage/audit/models.py` — add `AdminEvent` ORM; Alembic revision `005_admin_events.py` (additive only, no destructive ops; respects per-DB `audit/alembic.ini` from chunk 2).
   - `src/ai_steward_wiki/settings.py` — extend with `tenancy_mode: Literal['single', 'multi'] = 'single'`, `admin_chat_id: int | None = None`, `admin_elevation_ttl_minutes: int = 30`.
   - `src/ai_steward_wiki/tg/bot.py` — register `/start`, `/admin` command handlers; `/start` flows through `start_unknown_user` for non-allowlisted users (the AllowlistMiddleware from chunk 10 must be **bypassed only for `/start`**); other commands stay gated. Inline keyboard for admin approve/reject via aiogram CallbackQuery.
   - Hook `purge_expired_pending` into the APScheduler bootstrap (chunk 4) as a daily 0500 UTC maintenance job (matches existing pattern from chunk 11 `sweep_staging_job`).
4. **Quality gate**:
   - `uv run alembic -c alembic/audit/alembic.ini upgrade head`
   - `uv run pytest tests/unit/auth -q`
   - `uv run pytest tests/unit/tg -q`
   - `uv run pytest tests/unit -q` (full unit suite stays ≥212 passing post-chunk-11 baseline, expected +18–25 new tests)
   - `uv run python scripts/lint_onboarding.py --template templates/onboarding-intro.ru.md` exit 0
   - `make lint` (ruff + format + mypy --strict on `src/`)
   - `make grace-lint` 0 errors
   - `make total-test` exit 0
5. **Commit** — `feat(M-ONBOARD-ADMIN): onboarding pending_users + admin elevate + intro lint + TENANCY_MODE` with trailer `bd_id: aisw-zsg`.
6. **Post-commit** — update `breakdown.xml` RunState (CurrentChunk=13, ClosedChunks+=12), Notes append, close `bd aisw-zsg`.

## Out of scope

1. SIGHUP infra itself (chunk 3 owns it; this chunk only triggers it via existing API).
2. PII redaction of pending intro messages (chunk 13 / M-OPS-PII handles `pending_users.username`).
3. Multi-language intro — D-032 fixes MVP as ru-only; future i18n is out of scope.
4. Real Telegram callback round-trip in unit tier — use aiogram FSM/CallbackQuery test doubles; real round-trip lives in `RUN_INTEGRATION=1`.
5. Admin shadow channel **wiring** at every failure site — only the emitter API + Pydantic guard ship here. Call-sites added in chunks 13/14/16.
6. Demote-on-window-close grace handling — `is_elevated` simply returns False after `expires_at`; no proactive notification.
