# step-13-plan.md — Chunk 13 / M-OPS-PII

**bd_id:** aisw-13
**Module:** M-OPS-PII
**Window estimate:** 0.45
**Sources:** §10.4 (PII tier-классификация + Retention-таблица) of `docs/Spec-WIKI/research/tech-spec-draft.md`; D-034 (Tier-1/2/3 retention, write-time hook, GDPR-purge); D-035 (structlog JSON-lines, correlation_id через contextvars); D-033 (chat_log plaintext retention); D-041 (soft-delete trash 30d). Depends on chunk 2 (M-STORAGE, 3×SQLite + Alembic per-DB + Pydantic discriminated union) и chunk 4 (M-SCHED-CORE, APScheduler `AsyncIOScheduler` + queue + DLQ + maintenance-job taxonomy).

## Goal

Ship PII redaction at write-time для всех structured-log / DB-bound записей и retention-purge jobs по полной §10.4 таблице:

1. **Redactor** — единый `structlog` processor + reusable callable, применяющий NIST SP 800-122 tier-классификацию:
   - **Tier-1 DROP** (tokens, passwords, API keys, JWT, OAuth bearer, кредитные карты PAN, SSN-like, PEM private blocks) → подстановка `[REDACTED:tier1:<kind>]` без оригинала и без хэша.
   - **Tier-2 MASK** (email, phone E.164/local, IBAN, BIC, credit-card last4 как стенд-alone число) → shape-preserving mask + детерминированный `blake2b-128` hash с сервисной HMAC-солью из `settings.pii_hash_secret` для cross-ref (`[MASK:tier2:email:ab12…cd34]`). Соль читается через `SecretStr`, никогда не логируется.
   - **Tier-3 PLAINTEXT** — пропускает дальше; защита уровня store retention + unix `0600`.
2. **Coverage matrix** — каждый PII-чувствительный сайт пишет ИЛИ через `redact()` ИЛИ через structlog pipeline с зарегистрированным processor. Тестово-доказательно: grep-lint + property test.
3. **Retention purge jobs** — APScheduler maintenance-jobs из chunk 4 taxonomy (`maintenance` queue), по одной job на каждую строку §10.4 retention-таблицы где Purge mechanism = APScheduler:
   - `chat_log_purge` daily 04:00 UTC, retention 30d
   - `tg_updates_purge` hourly :07, retention 24h
   - `seen_files_purge` daily 04:10 UTC, retention 30d
   - `dedup_hits_purge` daily 04:15 UTC, retention 90d
   - `audit_purge` daily 04:20 UTC, retention 90d (target: `audit_events`)
   - `admin_events_purge` daily 04:25 UTC, retention 90d
   - `job_outputs_purge` daily 04:30 UTC, retention 90d
   - `run_outputs_purge` daily 04:35 UTC, retention 180d
   - `onboarding_purge` daily 04:40 UTC, retention 180d
   - `tracker_purge` daily 04:45 UTC, retention 90d
   - `pending_users_purge` daily 04:50 UTC, retention 14d (overlap с chunk 12 `purge_expired_pending`: реализуется здесь, chunk 12 cross-import; не дублировать)
   - `staging_purge` hourly :12, retention 24h (`Inbox-WIKI/raw/media/_staging/`)
   - `trash_purge` daily 04:55 UTC, retention 30d, tier-1 DROP / tier-2 MASK финальный sweep по содержимому `_trash/` перед физическим удалением (D-034 §10.4 п.2)
4. **GDPR-purge** API — admin-callable `purge_user(target_telegram_id, scope='all'|'wiki:<domain>')` с allow-list `audit.db.chat_log`, `audit.db.audit_events`, `audit.db.admin_events` (актор оставляем), `sessions.db.pending_users`, `<wiki>/raw/media/` (manual confirm), `_trash/<…>`. Не трогает `prompt_versions`, `run_outputs` (D-025 indefinite invariant), `users.toml`-snapshot. Возвращает counts per store. Гейтится через `admin.assert_admin` из chunk 12.
5. **Settings & wiring** — `pii_hash_secret: SecretStr`, `pii_drop_enabled: bool = True`, `pii_mask_enabled: bool = True`, `retention_dry_run: bool = False` (dry-run считает, но не удаляет — для первого staging-прогона). Все purge-jobs регистрируются в `scheduler/bootstrap.py` (chunk 4 хуку) при старте сервиса; idempotent на повторный регистрационный вызов (matches existing `add_job(..., replace_existing=True, jitter=30)` pattern).
6. **Audit trail** — каждая purge-job INSERT в `audit.db.audit_events` row `(ts_utc, event='retention.purge', store=<table>, deleted=<count>, oldest_kept_utc=<ts>, dry_run=<bool>, correlation_id=<job_run_id>)`. Соответствие taxonomy chunk 4: `category='maintenance'`, `priority=4`, no-confirm, no-shadow.

## Steps (TDD)

1. **Recon** — read:
   - `src/ai_steward_wiki/storage/audit/models.py` (chunk 2) → подтвердить наличие `chat_log`, `tg_updates`, `seen_files`, `dedup_hits`, `audit_events`, `admin_events`, `job_outputs`, `run_outputs`, `prompt_versions`, `onboarding_events`. Каждой нужна колонка `created_at_utc` (или эквивалент `ts_utc`) для `WHERE created_at_utc < now - retention`. Если у `dedup_hits` или `onboarding_events` нет — добавить additive Alembic revision `006_retention_columns.py` (только NOT NULL DEFAULT с CURRENT_TIMESTAMP, без backfill риска на пустой dev DB).
   - `src/ai_steward_wiki/storage/jobs/models.py` → `tracker_answers.answered_at_utc` для retention.
   - `src/ai_steward_wiki/storage/sessions/models.py` → `pending_users.expires_at` уже есть из chunk 12; используем его, retention = `expires_at < now`.
   - `src/ai_steward_wiki/scheduler/bootstrap.py` и `scheduler/taxonomy.py` (chunk 4) → API `register_maintenance_job(name, trigger, callable, jitter_s=30)`; если нет — расширить совместимо.
   - `src/ai_steward_wiki/observability/logging.py` (chunk 1) → processor chain order; redactor должен стоять **перед** JSONRenderer и **после** add_log_level/timestamp, чтобы маскировать оба msg и event-dict values.
   - `src/ai_steward_wiki/settings.py` → добавить новые поля.
   - `src/ai_steward_wiki/auth/admin.py` (chunk 12) → reuse `assert_admin` для GDPR endpoint.
2. **Tests RED** под `tests/unit/ops/pii/` и `tests/unit/ops/retention/`:
   - `test_redactor_tier1.py` — fixtures из NIST SP 800-122 примеров + локальные edge cases:
     - 16-digit PAN с пробелами/дефисами → `[REDACTED:tier1:card]`
     - JWT `eyJ...` (3 base64url segments) → `[REDACTED:tier1:jwt]`
     - PEM `-----BEGIN ... PRIVATE KEY-----` многострочный → `[REDACTED:tier1:pem]`
     - `Bearer <token>` HTTP header → `[REDACTED:tier1:bearer]`
     - Generic high-entropy `api_key=`, `password=`, `secret=` k/v → `[REDACTED:tier1:secret]`
     - **Negative:** обычное русское предложение, числа года, UUID-correlation-id — нетронуты.
   - `test_redactor_tier2.py`:
     - `user@example.com` → `[MASK:tier2:email:<16hex>]`; одинаковый email с одинаковой солью → одинаковый hash; разный → разный.
     - `+79161234567` / `8 (916) 123-45-67` оба нормализуются перед хэшированием → одинаковый hash.
     - `DE89370400440532013000` IBAN → `[MASK:tier2:iban:<16hex>]`, валидируется по mod-97.
     - Stable across processes (deterministic hash function).
   - `test_redactor_structlog.py` — processor встроен в chain; `logger.info("user signup", email="x@y.com", token="abc...")` → итоговый JSON не содержит ни `x@y.com`, ни `abc...`; содержит маркеры; `correlation_id` сохранён as-is.
   - `test_redactor_dropin.py` — `redact(text: str) -> str` идемпотентен (`redact(redact(x)) == redact(x)`); composable.
   - `test_retention_matrix.py` — параметризован по §10.4 retention-таблице; для каждой строки seed-данные с timestamps `now-N+1d` и `now-N-1d`; запуск job → `count=1` deleted, oldest kept ≥ `now - N`. Один canonical fixture-builder.
   - `test_retention_audit_trail.py` — после каждого job-run появляется row в `audit_events` с правильным `store=` и `deleted=`.
   - `test_retention_dry_run.py` — `retention_dry_run=True`: count считается, ничего не удаляется, audit row пишется с `dry_run=True`.
   - `test_gdpr_purge.py` — `purge_user(tg_id, scope='all')` удаляет из allow-listed stores, не трогает запрещённые (`prompt_versions`, `run_outputs` index, `users.toml`); возвращает корректные counts; non-admin → `NotAnAdmin`.
   - `test_trash_purge_final_sweep.py` — содержимое `_trash/<wiki>/data/runs/*.md` с tier-1 PAN перед физ.удалением проходит redactor с `mode='final-sweep'` (idempotent overwrite на месте) и аудит-запись `trash_final_sweep` фиксирует counts redacted.
3. **GREEN** — implement:
   - `src/ai_steward_wiki/ops/pii.py`:
     - Compiled regex set per tier (kept as module constants, no recompile per call); порядок: tier-1 → tier-2.
     - `PIIRedactor` dataclass с `hash_secret: bytes`, `drop_enabled`, `mask_enabled`; `redact(text: str) -> str`; `redact_event(event_dict: dict) -> dict` рекурсивно по str-значениям, не трогает не-str.
     - `make_structlog_processor(redactor)` returning `(logger, method, event_dict) -> event_dict` (chain-compatible).
     - `_hash_token(normalized: str, secret: bytes) -> str` — `hmac.new(secret, normalized.encode(), 'blake2b').hexdigest()[:16]`.
     - Phone/email/iban normalizers (digits-only / lowercase / strip-spaces) перед хэшем.
   - `src/ai_steward_wiki/ops/retention.py`:
     - `RetentionPolicy` Pydantic model: `store: str`, `retention: timedelta`, `cron_trigger: CronTrigger`, `delete_sql: str` (template), `audit_event: str = 'retention.purge'`.
     - `RETENTION_POLICIES: list[RetentionPolicy]` — single source of truth, **импортируется тестом** для параметризации (test и code читают один и тот же список → исключает drift).
     - `run_purge(policy: RetentionPolicy, *, dry_run: bool) -> PurgeResult` — открывает session для нужной DB (`audit` / `jobs` / `sessions`), `DELETE WHERE <ts_col> < :cutoff`, COUNT pre-delete, INSERT в `audit_events`. Использует savepoint per-job; ошибки логируются + DLQ (chunk 4 API), не падает на следующих jobs.
     - `register_retention_jobs(scheduler, policies, *, dry_run=False)` — `scheduler.add_job(run_purge, trigger=policy.cron_trigger, kwargs={'policy': policy, 'dry_run': dry_run}, id=f'retention.{policy.store}', jitter=30, replace_existing=True, max_instances=1, misfire_grace_time=600)`.
     - `purge_trash_sweep()` — отдельная job для `_trash/`, читает `_trash/<wiki>-<ts>/**/*.md` файлы старше cutoff, прогоняет содержимое через `redactor` в mode='final-sweep' (tier-1 DROP + tier-2 MASK) перед `shutil.rmtree`.
     - `purge_staging()` — файловая, удаляет `Inbox-WIKI/raw/media/_staging/<file>` где mtime < now-24h.
   - `src/ai_steward_wiki/ops/gdpr.py`:
     - `PurgeUserResult = dict[str, int]`
     - `purge_user(target_tg_id: int, *, actor_tg_id: int, scope: Literal['all'] | str, admin_svc: AdminService) -> PurgeUserResult` — `admin_svc.assert_admin(actor_tg_id)`; allow-listed stores per scope; INSERT `admin_events` row `action='gdpr_purge'`.
   - `src/ai_steward_wiki/settings.py` — добавить `pii_hash_secret: SecretStr`, `pii_drop_enabled: bool = True`, `pii_mask_enabled: bool = True`, `retention_dry_run: bool = False`. Загрузка соли через systemd-credentials в production (см. chunk 16), `.env` в dev.
   - `src/ai_steward_wiki/observability/logging.py` — вставить `redactor.make_structlog_processor(...)` после `add_logger_name`, до `JSONRenderer`.
   - `src/ai_steward_wiki/scheduler/bootstrap.py` — на старте вызвать `register_retention_jobs(scheduler, RETENTION_POLICIES, dry_run=settings.retention_dry_run)`.
   - `alembic/audit/versions/006_retention_columns.py` — additive `created_at_utc` для таблиц, у которых её не оказалось при recon (no-op revision если уже есть).
   - `scripts/lint_pii_coverage.py` — grep-lint: ищет `logger.(info|warning|error|debug)\(.*\b(email|phone|password|token|secret|api_key)\b` в `src/` где **нет** аргумента, проходящего через `redactor` или зарегистрированный processor. Exit 1 при находке.
4. **Quality gate**:
   - `uv run alembic -c alembic/audit/alembic.ini upgrade head`
   - `uv run pytest tests/unit/ops/pii -q`
   - `uv run pytest tests/unit/ops/retention -q`
   - `uv run pytest tests/unit -q` (полный unit-сьют зелёный)
   - `uv run python scripts/lint_pii_coverage.py` exit 0
   - `make lint` (ruff + format --check + mypy --strict on `src/`)
   - `make grace-lint` 0 errors
   - `make total-test` exit 0
5. **Commit** — `feat(M-OPS-PII): tiered redactor + retention purge jobs + GDPR purge` с трейлером `bd_id: aisw-13`.
6. **Post-commit** — update `breakdown.xml` RunState (CurrentChunk=14, ClosedChunks+=13), Notes append, close `bd aisw-13`.

## Out of scope

1. At-rest crypto для SQLite — §10.4 явно откладывает до `TENANCY_MODE=multi` или появления tier-1/2 PII вне redactor. Триггеры пересмотра задокументированы, реализация — нет.
2. Off-site / 3-2-1 / GFS бэкап sweep — owned by chunk 14 (M-OPS-BACKUP).
3. Per-WIKI git `gitleaks` hook — chunk 14.
4. `<wiki>/data/runs/` retention — D-025 invariant *indefinite* в MVP; future decision отдельная.
5. `<wiki>/raw/media/` automatic GC — §10.4 marks manual via NL `purge_wiki` / admin GDPR purge; admin-path реализуется здесь, automatic GC — нет.
6. Real-time PII detection в text → Claude CLI пайплайн (Inbox/Wiki runs) — redactor применяется только к **service-side** structured logs и DB-bound записям, не к содержимому WIKI-файлов (D-022 immutable raw, D-025 runs invariant).
7. ML/NER-классификатор PII — regex-only MVP; ML — future если приедут tier-1/2 не покрытые regex.
