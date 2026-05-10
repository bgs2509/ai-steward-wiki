---
title: "Tech Spec Draft — ai-steward-wiki"
type: research
status: stable
date: 2026-05-10
sources:
  - ../raw/20260507-ai-steward-wiki-only-overview.md
  - ../decisions/D-001-time-tracker-vs-job-model.md
  - ../decisions/D-002-job-model-storage.md
  - ../decisions/D-003-scheduler-backend.md
  - ../decisions/D-004-inbox-wiki-scope.md
  - ../decisions/D-005-no-planner-json.md
  - ../decisions/D-006-state-storage-layout.md
  - ../decisions/D-007-add-dir-scope.md
  - ../decisions/D-008-wiki-marker-format.md
  - ../decisions/D-009-classifier-engine.md
  - ../decisions/D-010-nl-time-parsing.md
  - ../decisions/D-011-concurrent-claude.md
  - ../decisions/D-012-wiki-lock.md
  - ../decisions/D-013-claude-cli-auth.md
  - ../decisions/D-014-tracker-memory-model.md
  - ../decisions/D-015-system-prompt-inject.md
  - ../decisions/D-016-inbox-claude-md-template.md
  - ../decisions/D-017-domain-claude-md-template.md
  - ../decisions/D-018-ingest-idempotency.md
  - ../decisions/D-019-cron-failure-mode.md
  - ../decisions/D-020-cron-result-routing.md
  - ../decisions/D-021-timeouts-kill-policy.md
  - ../decisions/D-022-voice-photo-input.md
  - ../decisions/D-023-tg-confirmations.md
  - ../decisions/D-024-digest-format.md
  - ../decisions/D-025-output-size.md
  - ../decisions/D-026-tg-streaming.md
  - ../decisions/D-027-anti-nesting-admin-boundary.md
  - ../decisions/D-028-admin-access.md
  - ../decisions/D-029-wiki-init-auth.md
  - ../decisions/D-030-onboarding.md
  - ../decisions/D-031-allowlist-hot-reload.md
  - ../decisions/D-032-multi-language.md
  - ../decisions/D-033-chat-history.md
  - ../decisions/D-034-pii-redactor.md
  - ../decisions/D-035-service-logging.md
  - ../decisions/D-036-testing-strategy.md
  - ../decisions/D-037-git-in-wiki.md
  - ../decisions/D-038-per-user-systemd.md
  - ../decisions/D-039-claude-md-evolution.md
  - ../decisions/D-040-log-date-format.md
  - ../decisions/D-041-no-direct-wiki-commands.md
  - ../decisions/D-042-unify-user-config.md
---

# Tech Spec Draft — ai-steward-wiki

> Сводное техническое описание, агрегирующее **42 артефакта-решения** (`D-001…D-042`):
> - 41 active + 1 superseded (`D-029` → `D-041`).
> - Источник истины по архитектурным выборам — соответствующие `decisions/D-NNN-*.md`.
> - Этот документ — *навигационная карта*, не SSoT.

## 0. Edit invariants (mandatory pre-commit checklist)

> **Назначение:** этот блок — closed checklist, который ОБЯЗАН пройти каждый редактор `tech-spec-draft.md` (человек или агент) перед коммитом. Цель — превратить «не забыть про SSoT» в воспроизводимый ритуал и закрыть pattern повторяющихся drift-фиксов (см. git history 2026-05-09…2026-05-10, коммиты `7abeb0b`, `f0de8b2`, `3af9608`, `d85e3f5`, `30eb2fa`).
>
> Каждый item — *grep-проверяемый* (regex или явное перечисление). Если хоть один пункт нарушен — править доку перед commit.

**INV-1. D-006 schema coverage.** Каждая таблица, перечисленная в `decisions/D-006-state-storage-layout.md` §"Структура трёх БД", ОБЯЗАНА иметь строку в §10.4 retention-таблице. Closed list (на 2026-05-10):
1. `audit.db`: `chat_log`, `audit_events`, `admin_events`, `tg_updates`, `seen_files`, `dedup_hits`, `job_outputs`, `run_outputs`, `prompt_versions`, `onboarding_events`.
2. `sessions.db`: `users`, `pending_users`, `pending_confirms`, `inbox_hint_cache`, `fsm`.
3. `jobs.db`: `tracker_answers` (плюс служебные APScheduler — out of scope retention).
4. Файловые store: `Inbox-WIKI/raw/media/_staging/`, `state/snapshots/`, `<wiki>/data/runs/`, `<wiki>/raw/media/`, `_trash/<Domain>-WIKI-<ts>`.

**INV-2. Job kinds → priority lanes (closed table).** §3 ОБЯЗАН содержать таблицу «Job kinds → priority lanes» со столбцами `kind | lane | lane id | CLI? | WIKI workspace | DLQ | Timeout | источник`. Каждый `kind` из §6 (fast-path), §10 (maintenance) и из `decisions/D-002-job-model-storage.md` ОБЯЗАН быть отдельной строкой этой таблицы **до** первого упоминания в payload-валидации или коде. Текущий closed set: `interactive`, `reminder_job`, `wiki_job`, `ingest_job`, `digest_job`, `*_purge`, `db_snapshot`. Добавление нового kind = одновременная правка §3 таблицы + §2 Pydantic union + (если нужно) retention-строки в §10.4.

**INV-3. Backup §10 ↔ D-037 «git ≠ DR».** §10.2 НЕ ДОЛЖЕН утверждать, что git-remote-push является backup-механизмом. Любое упоминание `git push` в backup-контексте → нарушение. SSoT — `decisions/D-037-git-in-wiki.md` §"Remote push" п.1.

**INV-4. seen_files / tg_updates location.** Оба слоя idempotency живут в `audit.db`, не в `jobs.db`. SSoT — `decisions/D-018-ingest-idempotency.md` §"Уточнение 2026-05-10".

**INV-5. Identity vocabulary.** §2 правило строго:
1. `telegram_id` — canonical external user id;
2. `owner_telegram_id` — только колонка `jobs`;
3. `chat_id` — только delivery target;
4. `user_id` — только internal DB surrogate (`sessions.db`).

Любое смешение (`user_id` как external, `chat_id` как identity) → нарушение. Cross-store mapping `sessions.users.user_id ↔ telegram_id` — invariant таблицы `sessions.users` (D-031/D-042).

**INV-6. Auth isolation.** Stage-0 default — Claude CLI Haiku с shared `CLAUDE_CONFIG_DIR` (D-013). Любой direct Anthropic API backend (`STAGE0_BACKEND=anthropic_api`) ОБЯЗАН использовать ОТДЕЛЬНЫЙ credential через systemd-credentials/secret-manager — НЕ переиспользует Claude Code OAuth. SSoT — D-013 + `decisions/D-009-classifier-engine.md`.

**INV-7. WIKI lifecycle = NL only.** В §4/§5/§11 НЕ ДОЛЖНО появляться явных команд `/wiki_init`, `/wiki_delete`, `/wiki_restore`, `/wiki_purge`, `/wiki_rename`, `/wiki_merge`, `/wiki_split`. Read-only `/wiki_list`, `/wiki_show` — допустимы. SSoT — `decisions/D-041-no-direct-wiki-commands.md` (D-029 superseded).

**INV-8. Tech-spec ≠ SSoT.** Каждое числовое значение (timeout, retention, threshold, limit), кроме сводных таблиц, ОБЯЗАНО ссылаться на конкретный D-NNN. При расхождении число в D-файле побеждает — править tech-spec, не D-файл.

**INV-9. Audit/sessions tables → minimal schema sketch.** Каждая таблица из `audit.db` или `sessions.db`, упомянутая в любом разделе tech-spec за пределами retention-таблицы §10.4, ОБЯЗАНА иметь минимальный schema sketch (список колонок с типами, PK/FK, UNIQUE) либо непосредственно в §2 «Прочее», либо явную ссылку `(детальные DDL — в D-NNN)`. Цель: устранить класс багов «таблица упомянута, но никто не знает её структуру». Closed list таблиц, для которых sketch ОБЯЗАН быть в §2: `job_outputs` (D-020), `run_outputs` (D-025), `inbox_hint_cache` (D-006 §"Структура трёх БД" п.5 уже даёт полную сигнатуру). Остальные — допустимо ограничиться ссылкой на D-NNN при условии, что D-файл содержит DDL.

**INV-10. Cross-store FK convention.** Поскольку SQLite не поддерживает FK между БД, mapping `user_id ↔ telegram_id` живёт *только* в `sessions.db.users`. Любое упоминание `FOREIGN KEY ... REFERENCES users(user_id)` из таблицы `jobs.db.*` или `audit.db.*` — нарушение. SSoT — §2 «Cross-store identity mapping invariant». Verify: `grep -nE 'jobs\.db\.[a-z_]+\.user_id\s*FK|audit\.db\.[a-z_]+\.user_id\s*FK' research/tech-spec-draft.md` → пусто.

**INV-11. DLQ location.** §3 ОБЯЗАН явно называть DLQ-таблицу с DB-prefix (`jobs.db.jobs_dlq`). Любое упоминание DLQ без DB → нарушение. SSoT — D-019.

**INV-12. Failure-strike includes timeout.** §3 ОБЯЗАН явно фиксировать, что `killed`-by-timeout считается failure-strike (counter увеличивается, reset только на success). Без этой фразы Critical Middle gap «timeout не учитывается?» воспроизводится. SSoT — D-019 + D-021.

**INV-13. Prompts path absolute.** Любое упоминание `prompts/<file>.md` ОБЯЗАНО быть в форме `<service>/prompts/<file>.md` (или абсолютного `/opt/ai-steward-wiki/prompts/`), согласованного с `ReadOnlyPaths=<service>/prompts` из §10.1. Verify: `grep -nE '^\s*-\s*\`prompts/' research/tech-spec-draft.md` → пусто (только `<service>/prompts/`).

**INV-14. Anti-spam numbers SSoT in §5.** §5 ОБЯЗАН содержать секцию «Anti-spam» с тремя элементами (hard cap 20, Levenshtein ≤2 с graduated confirm, `_trash/` exclusion из counter/autodiscover/walk). Числа — ссылка на D-041. Без этой секции D-041 SSoT теряется в навигационной карте.

**Verification ritual (перед commit):**
1. Прочитать D-006 §"Структура трёх БД" → diff против §10.4 retention table → INV-1 ✅.
2. `grep -nE "kind='[a-z_]+'" research/tech-spec-draft.md` → каждый matched kind ∈ §3 closed table → INV-2 ✅.
3. `grep -nE 'git push|remote push' research/tech-spec-draft.md` → ничего в backup-контексте → INV-3 ✅.
4. `grep -n 'jobs.db.seen_files\|jobs.db.tg_updates' research/tech-spec-draft.md` → пусто → INV-4 ✅.
5. `grep -nE '/wiki_(init|delete|restore|purge|rename|merge|split)' research/tech-spec-draft.md` → пусто → INV-7 ✅.
6. Каждое число в §3/§5/§8/§10 имеет ссылку `(D-NNN)` или `(см. §X)` → INV-8 ✅.
7. Для каждой таблицы из `audit.db`/`sessions.db`, упомянутой вне §10.4, → §2 содержит schema sketch ИЛИ ссылку `(детальные DDL — в D-NNN)` → INV-9 ✅.
8. `grep -nE 'REFERENCES users\(user_id\)' research/tech-spec-draft.md` → ни одного match вне §2/§10.4 sessions.db-контекста → INV-10 ✅.
9. `grep -n 'DLQ' research/tech-spec-draft.md` → каждая строка содержит `jobs.db.jobs_dlq` или ссылку на §3 → INV-11 ✅.
10. `grep -n 'timeout' research/tech-spec-draft.md` в §3 контексте → присутствует фраза о counted-as-failure-strike → INV-12 ✅.
11. `grep -nE '\` *prompts/' research/tech-spec-draft.md` → пусто; все вхождения — `<service>/prompts/` → INV-13 ✅.
12. §5 содержит подсекцию «Anti-spam» с тремя элементами + ссылка на D-041 → INV-14 ✅.

При нарушении любого INV — фикс tech-spec ИЛИ обновление этого checklist (если invariant перестал быть актуален из-за нового D-файла). Изменение самого checklist логируется в `log.md` как `refactor`.

---

## 1. Executive Summary & Scope

`ai-steward-wiki` — **изолированный мультипользовательский Telegram-сервис** на отдельной VPS, который превращает Claude Code CLI в персонального WIKI-ассистента в стиле метода Карпаты.

**Базовый поток:**

1. Юзер общается с ботом естественным языком — *текст, файл, фото, голос*.
2. Без явного указания папок и команд.
3. Бот идентифицирует автора по `telegram_id`.
4. Классифицирует контент через pipeline **Stage-0 Haiku → Stage-1a/1b Sonnet**.
5. Запускает Claude в нужной `<Domain>-WIKI/`.

**Изоляция от `ai-steward`** (см. CLAUDE.md §1.1):

- Нет общего volume.
- Нет миграции `planner.json`.
- Нет cross-service чтений.
- *Нулевая связь по умолчанию.*

**MVP-объём:**

1. **Single-tenant** Henry-N.
2. **Subscription auth.**
3. Классификатор + scheduler + per-WIKI lifecycle через NL-промпты.
4. **Voice/photo input.**
5. **Streaming-ответы** в TG.

**Решения этого слоя:**

- [overview §1–§1.1](../raw/20260507-ai-steward-wiki-only-overview.md) — назначение, граница с `ai-steward`, базовый поток.
- [D-007](../decisions/D-007-add-dir-scope.md) — `--add-dir` scope: per-WIKI workspace, auto-walk `USERS/<USER>/CLAUDE.md` для профиля.
- [D-013](../decisions/D-013-claude-cli-auth.md) — single-tenant subscription mode, все юзеры = один реальный человек.
- [D-027](../decisions/D-027-anti-nesting-admin-boundary.md) — `WORKSPACE_ROOT` как единый anchor всей файловой иерархии.
- [D-041](../decisions/D-041-no-direct-wiki-commands.md) — lifecycle WIKI только через NL-промпт, прямых команд нет.

---

## 2. Data Model & Storage

State сервиса разнесён по **трём SQLite-БД** с разными bounded contexts:

1. **`jobs.db`** — горячая операционная.
2. **`audit.db`** — append-only.
3. **`sessions.db`** — runtime-state.

**Унифицированная таблица `jobs`** — Flat + typed JSON payload:

- Общие колонки индексируются: `id`, `kind`, `owner_telegram_id`, `cron_expr`, `enabled`, `mandatory`, `follow_up_delay_min`, …
- `kind`-специфика валидируется **Pydantic discriminated union** на boundary без миграций на каждый новый kind.

**Identity vocabulary** (из [D-042](../decisions/D-042-unify-user-config.md)):

- `telegram_id` — **canonical** external user id.
- `owner_telegram_id` — допустим *только* как `jobs` owner column.
- `chat_id` — *только* delivery target.
- `user_id` — *только* internal DB surrogate.

**Cross-store identity mapping invariant.** SQLite per-DB изоляция запрещает FOREIGN KEY между БД, поэтому mapping поддерживается *по convention*:

1. `sessions.db.users` — единственная таблица, держащая обе колонки: `user_id INTEGER PK AUTOINCREMENT` (surrogate) и `telegram_id INTEGER UNIQUE NOT NULL` (canonical).
2. Любая таблица в `jobs.db` или `audit.db`, ссылающаяся на пользователя, использует `telegram_id` *напрямую* (не `user_id`). Исключение — таблицы, которые сами живут в `sessions.db` (`pending_users`, `pending_confirms`, `inbox_hint_cache`, `fsm`) — там FK на `users.user_id` legal.
3. Lookup из cross-DB-контекста (`audit.db.audit_events.actor_telegram_id` → human-readable name) идёт через `sessions.db.users` join в application-layer, не SQL-уровне.
4. Это инвариант — нарушение (FK на `user_id` из `jobs.db`/`audit.db`) ломает sync `users.toml` через SIGHUP (D-031), потому что `user_id` пересоздаётся при validate-before-swap.

**Прочее:**

- **Tracker-память** — append-only таблица в `jobs.db`; top-3 предсказания вычисляются *on-the-fly* SQL-запросом по 90-дневному окну.
- `audit.db` также держит `job_outputs` и `run_outputs`, чтобы routing/delivery metadata не терялась между D-020/D-025 и storage map. Минимальные схемы (детальные DDL — в D-020/D-025):
  1. `job_outputs(id PK, job_id FK→jobs.db.jobs.id, run_id, fired_at_utc, finished_at_utc, status [ok|fail|timeout|killed], notify_policy [always|on_output|silent], delivered_to_chat_id, exit_code, error_class, payload_sha256)` — retention **90d** (см. §10.4). SSoT — D-020.
  2. `run_outputs(id PK, run_id UNIQUE, job_id FK, wiki_id, owner_telegram_id, started_at_utc, finished_at_utc, output_path TEXT, output_bytes, output_sha256, summary_chars, kind [reply|digest|ingest_report])` — индекс файлов в `<wiki>/data/runs/<YYYY-MM-DD>/<run_id>.md`. Retention индекса **180d** (см. §10.4); content в файлах indefinite. SSoT — D-025.
- Все БД работают в **WAL + NORMAL + foreign_keys** с `busy_timeout=5000`.
- Миграции — **Alembic per-DB**.

**Решения:**

- [D-002](../decisions/D-002-job-model-storage.md) — `jobs` = одна таблица, общие колонки + JSON `payload`, валидация Pydantic.
- [D-006](../decisions/D-006-state-storage-layout.md) — три раздельных SQLite, Alembic per-DB, WAL.
- [D-014](../decisions/D-014-tracker-memory-model.md) — `tracker_answers` append-only в `jobs.db`, top-3 on-the-fly SQL, retention 90д.

---

## 3. Job / Scheduler Core

**Scheduler-ядро** = APScheduler `AsyncIOScheduler` + `SQLAlchemyJobStore` в том же процессе бота, persistence в `jobs.db` через общий engine.

**Pipeline:**

1. APScheduler **не запускает CLI напрямую** — он `put`'ит задачу в `asyncio.PriorityQueue`.
2. Очередь имеет **5 уровней приоритета**:
   1. `interactive=0`
   2. `user_write=1`
   3. `cron_write=2`
   4. `digest=3`
   5. `ingest=4`
3. Worker'ы тянут задачи под `Semaphore(MAX_CONCURRENT_CLI=4)` и **per-WIKI lock**'ом.

**Failure-handling:**

- Taxonomy: `Transient` / `Permanent` / `Unknown`.
- **Exp.backoff retry** (override per-category в payload).
- **DLQ-таблица** `jobs.db.jobs_dlq` (живёт рядом с `jobs`, общий engine; FK `jobs_dlq.job_id → jobs.id`); manual `/retry` (admin-only). SSoT — D-019.
- **Auto-disable** recurring job'а после **3 подряд провалов** любой категории `Transient | Permanent | Unknown` (D-019). **Timeout (`killed` через SIGTERM/SIGKILL из §3 Kill-sequence) учитывается как failure-strike** — это `Transient` по умолчанию, но 3 подряд timeout'а так же дисэйблят job. Reset счётчика — только на successful run.

**Timeout'ы** (per-category hard limits, каноническая таблица в [D-021](../decisions/D-021-timeouts-kill-policy.md)):

- `Haiku` — 15s
- `wiki_job` — 120s
- `digest` — 600s

**Kill-sequence:** `SIGTERM → 10s → SIGKILL` + UX-cancel через inline-кнопку.

**Job kinds → priority lanes (closed taxonomy, satisfies INV-2):**

| `kind`           | Lane            | Lane id | CLI? | WIKI workspace | DLQ | Timeout (D-021) | Источник                      |
|------------------|-----------------|---------|------|----------------|-----|-----------------|-------------------------------|
| `interactive`    | `interactive`   | 0       | yes  | resolved       | yes | `wiki_job` 120s | router runtime (Stage-1a/1b)  |
| `reminder_job`   | `user_write`    | 1       | **no** | none (`wiki_id=null`) | yes | n/a (TG-deliver only) | fast-path §6, D-002, D-010 |
| `wiki_job`       | `cron_write`    | 2       | yes  | resolved       | yes | 120s            | D-002, D-019, D-020           |
| `ingest_job`     | `ingest`        | 4       | yes  | resolved       | yes | 120s            | D-002, D-018                  |
| `digest_job`     | `digest`        | 3       | yes  | resolved       | yes | 600s            | D-024, D-025                  |
| `*_purge` / `db_snapshot` | `ingest` | 4       | no   | none           | no  | 60s (internal)  | §10.2, §10.4 retention jobs   |

**Правила:**

1. Любой новый `kind` ОБЯЗАН появиться в этой таблице **до** первого использования в коде или payload-валидации (Pydantic discriminated union из §2).
2. `reminder_job` — единственный kind с `wiki_id=null` и без CLI-вызова; firing-handler доставляет TG-message напрямую (см. §6 fast-path).
3. `*_purge`/`db_snapshot` — internal maintenance, не user-facing; не отображаются в `/jobs_list`.

**Решения:**

- [D-001](../decisions/D-001-time-tracker-vs-job-model.md) — time-tracker = надстройка над `job-model`.
- [D-003](../decisions/D-003-scheduler-backend.md) — APScheduler in-process, общий engine с `jobs.db`.
- [D-019](../decisions/D-019-cron-failure-mode.md) — error taxonomy + retry + DLQ + auto-disable.
- [D-020](../decisions/D-020-cron-result-routing.md) — `notify_policy` per-job (`always|on_output|silent`) + admin shadow channel.
- [D-021](../decisions/D-021-timeouts-kill-policy.md) — hard timeouts + kill-sequence + UX `/cancel`.

---

## 4. Inbox & Routing

**Расположение:**

- Inbox-WIKI лежит **per-user** в `USERS/<NAME>/Inbox-WIKI/`.
- `CLAUDE.md` рендерится из shared template в репо при materialize.

**Inbox hint:**

1. Каждая `<Domain>-WIKI/CLAUDE.md` *обязана* содержать секцию `## Inbox hint`.
2. Эту секцию читает **router-Sonnet** (Stage-1a) для построения runtime-каталога доменов.
3. Сам Inbox-`CLAUDE.md` доменов *не знает* — только мета-правила, intent-таксономия и universal pre-flight.

**Hint-cache:**

- Runtime-каталог доменов кэшируется в `sessions.db.inbox_hint_cache` per-user.
- В WIKI-файлы *не материализуется*.
- **Cache-ключ:** `(user_id, wiki_path)`, где `user_id` — internal sessions surrogate.
- **Metadata guards:** `size_bytes`, `mtime_ns`, `ctime_ns`.
- **Truth guard:** `content_sha256`.

**Hot-path алгоритм:**

1. `stat(CLAUDE.md)` совпал по всем metadata guards → **cache hit** без чтения файла.
2. На metadata mismatch — `read + sha256`:
   1. Если `sha256 == cache.content_sha256` → обновить только metadata guards (touch/rsync без re-parse).
   2. Иначе → re-parse `## Inbox hint` секции (regex `^## Inbox hint\s*$` до следующего `^## ` или EOF) → atomic swap (`size_bytes, mtime_ns, ctime_ns, content_sha256, hint_text`).
3. `ctime_ns` закрывает preserved-mtime сценарии: пользователь *может* сохранить `mtime`, но *не может* вручную выставить Linux `ctime`.

**Экономия cache:**

- Без cache: `N×file-read + N×3500 tokens` на каждое сообщение при 20 WIKI.
- С cache: **0 чтений** в hot-path, **~80% TTFB-экономия**.

**Idempotency** — двухслойная без LLM, оба слоя в `audit.db`:

1. **L1** — TG-update dedup: `audit.db.tg_updates`, **TTL 24h**.
2. **L2** — content-hash SHA-256: `audit.db.seen_files`, **TTL 30d**, inline-confirm на коллизии.

**Lifecycle WIKI:** прямых команд *нет* — все важные операции (create/delete/restore/rename/merge/split/edit-rules/edit-persona/purge + page-level) проходят **5-шаговый pre-flight** ([D-041](../decisions/D-041-no-direct-wiki-commands.md)):

1. Intent-grounding.
2. Blind-spot scan.
3. Clarification.
4. Confirm.
5. Execute + audit.

**Решения:**

- [D-004](../decisions/D-004-inbox-wiki-scope.md) — Inbox per-user + shared template, materialize at init.
- [D-016](../decisions/D-016-inbox-claude-md-template.md) — Inbox hint, closed intent vocabulary, universal pre-flight.
- [D-018](../decisions/D-018-ingest-idempotency.md) — двухслойный dedup; оба слоя в `audit.db` (amended 2026-05-10).
- [D-041](../decisions/D-041-no-direct-wiki-commands.md) — нет `/wiki_init|delete|restore|purge`, только NL-промпт + read-only `/wiki_list`, `/wiki_show`.

---

## 5. Wiki Lifecycle

Жизненный цикл WIKI-папки управляется **через NL-промпты** в Inbox-WIKI router'у с обязательным graduated explicit confirm для destructive операций.

**Нормализация имени** — pipeline свободного имени из NL-промпта (`health`, `Health Lite`, `health-lite`, `здоровье`):

1. Транслитерация `Cyrillic → Latin` (single-strategy ISO 9).
2. Split по non-alphanumeric.
3. PascalCase join (`health lite` → `HealthLite`).
4. Suffix `-WIKI` (`HealthLite-WIKI`).
5. Валидация regex `^[A-Z][A-Za-z0-9]*-WIKI$` (D-008).

**Lookup пресета** (§11) — case-insensitive:

- По slug `healthlite`, **ИЛИ**
- По `health-lite`-варианту с восстановленным дефисом для имени файла шаблона.
- Если `templates/healthlite.md` отсутствует и `templates/health-lite.md` существует — используется он.
- Иначе → `_default.md`.
- Mapping `slug → wiki-name` логируется в `audit.db` на create.

**Anti-spam (D-041 SSoT):**

1. **Hard cap 20 WIKI/user** — warn at 16/20, hard reject at 20/20 (см. §5 «Soft-limit»).
2. **Levenshtein ≤2** против существующих slug'ов того же юзера (включая `_trash/`-имена, **не** учитывая `_trash/` в hard cap) — на create router предупреждает «возможно, ты имел в виду `<existing>`?» с 3-кнопочным graduated confirm (D-023). Подтверждённый дубль создаётся, отклонённый — отменяется.
3. `_trash/` исключается из autodiscover, soft-limit counter и anti-nesting walk; учитывается *только* в Levenshtein-проверке (anti-name-collision при restore).

**Soft-delete:**

1. Папка переносится в `USERS/<NAME>/_trash/<Domain>-WIKI-<ts>/`.
2. **Trash-retention 30 дней** — отдельный механизм от PII tier-3 retention в §10.
3. **PII-redactor проходит и по `_trash/`** на soft-delete trigger:
   - Финальный tier-1 **DROP** / tier-2 **MASK** sweep ([D-034](../decisions/D-034-pii-redactor.md) §"Trash sweep").
   - Tier-3 plaintext остаётся под общей retention 30d.
   - По истечении — hard-delete: `shred -u` для media, `unlink` для md.
   - Audit-event: `trash_purged`.
4. Возможность восстановления — NL-промптом (intent `restore_wiki`).
5. `_trash/` *исключается* из autodiscover, soft limit (20 WIKI/user) и anti-nesting walk.

**Имя WIKI:** валидируется regex `^[A-Z][A-Za-z0-9]*-WIKI$` (strict whitelist) — единая convention WIKI-маркера.

**Soft-limit:**

- **20 WIKI/user.**
- Warn at 16, hard reject at 20.
- `_trash/` *не учитывается*.

**`CLAUDE.md` versioning:** каждый сгенерированный `CLAUDE.md` несёт:

- YAML frontmatter: `schema_version`, `template_id`, `last_migrated_at`, `template_sha256`.
- Разделение на **managed/user-zone** секции через HTML-комменты.
- Миграции применяют **3-way merge** (declarative) ИЛИ imperative `vN_to_vM.py` для нелинейных изменений.

**Решения:**

- [D-005](../decisions/D-005-no-planner-json.md) — `planner.json` *не существует* нигде в дереве; единственный SSoT — `jobs.db`.
- [D-008](../decisions/D-008-wiki-marker-format.md) — regex как единое правило WIKI-маркера.
- [D-039](../decisions/D-039-claude-md-evolution.md) — frontmatter + managed/user-zone + linear migration chain.
- *History:* [D-029](../decisions/D-029-wiki-init-auth.md) — исходный CRUD-design, **полностью superseded** D-041. Числовые механизмы (soft-limit 20 / warn at 16, Levenshtein ≤2, retention 30d, exclusion из autodiscover) явно зафиксированы как новый SSoT в [D-041](../decisions/D-041-no-direct-wiki-commands.md) §"Anti-spam защита".

---

## 6. Classifier & NLP

Classifier — **трёхступенчатый pipeline** с disambiguated терминологией (refactor 2026-05-10):

1. **Stage-0 = `classifier-Haiku`.**
   - Default backend в subscription-only MVP — headless Claude CLI Haiku:
     ```
     claude --model claude-haiku-4-5 \
            --output-format json \
            --json-schema <classifier-json-schema> \
            --max-turns 1 \
            --disallowedTools "Bash" "Read" "Write" "Edit" "Glob" "Grep" "WebFetch" \
            --permission-mode dontAsk
     ```
   - Shared Claude Code auth из [D-013](../decisions/D-013-claude-cli-auth.md).
   - **Optional** direct Anthropic SDK/API backend разрешён *только* при отдельном `STAGE0_BACKEND=anthropic_api` и отдельном API credential, *не* из Claude Code OAuth.
   - Возвращает: `{intent, confidence, distilled_payload}`.

2. **Stage-1a = `router-Sonnet`** (Inbox-context).
   - `claude --model sonnet -p ... --add-dir <Inbox-WIKI>` с подгруженным runtime-каталогом доменов из hint-cache (см. §4).
   - Возвращает: целевую `<Domain>-WIKI` + final intent после pre-flight clarification.

3. **Stage-1b = `executor-Sonnet`** (Domain-context).
   - `claude --model sonnet -p ... --add-dir <Domain-WIKI>` с domain-CLAUDE.md и Karpathy-режимом (ingest/query/lint).
   - Производит side-effects на содержимое WIKI.

**Fast-path** — прямая запись в `jobs` без Stage-1a/1b:

- Условие: `intent=reminder` & `confidence ≥ 0.85` & время распарсено.
- `kind='reminder_job'`.
- **Reminder-job не привязан к WIKI:**
  1. `owner_telegram_id` берётся из TG-update.
  2. `wiki_id` payload-поле = `null`.
  3. При срабатывании firing-handler доставляет TG-message *напрямую* без `<Domain>-WIKI` workspace и без CLI-вызова.
- Это сознательная asymmetry:
  - `reminder_job` — pure scheduler→TG.
  - `wiki_job` / `ingest_job` / `digest_job` — требуют resolved WIKI и проходят полный pipeline.

Иначе: `Stage-0 → Stage-1a → (опц.) Stage-1b`.

**NL-парсинг времени** — двухступенчатый:

1. `dateparser` (rule-based, ~50ms, ru/en, user-TZ из `users.toml` per D-042).
2. На промах — **Haiku-fallback** с узким system-промптом.
3. На ambiguous — эскалация в Stage-1a «уточни время».
4. Все datetime в `jobs.db` хранятся в **UTC**, user-TZ применяется *только* на input/output.

**System-prompt inject** — Hybrid:

**Базовая директория prompts** — `/opt/ai-steward-wiki/prompts/` (relative `<service>/prompts/` per §10.1 `ReadOnlyPaths=<service>/prompts`). Раскладка фиксирована (closed list):

- `<service>/prompts/classifier.md` — backend-independent для Stage-0:
  - Default CLI backend → `--append-system-prompt @file`.
  - Optional API backend → SDK/API system instructions.
- `<service>/prompts/wiki.md` — для всех Stage-1a/1b через `--append-system-prompt @file`.
- `<service>/prompts/inbox.md` — добавляется к Stage-1a.
- `<service>/prompts/domain-<type>.md` — добавляется к Stage-1b (`<type>` ∈ {health, health-lite, investment, budget, family, study, career, home, hobby, recipes, _default}).
- **Semver + sha256** всех prompt-файлов логируются в `audit.db.prompt_versions` на каждый model-call (D-015).
- Mount режим — RO (см. §10.1); CLI scope не может писать в `prompts/`.

**Решения:**

- [D-009](../decisions/D-009-classifier-engine.md) — Stage-0 Haiku → Stage-1 CLI Sonnet, threshold 0.85.
- [D-010](../decisions/D-010-nl-time-parsing.md) — `dateparser` → Haiku fallback → Stage-1 escalation; UTC storage, user-TZ обязателен.
- [D-015](../decisions/D-015-system-prompt-inject.md) — Hybrid prompts; backend-independent classifier; semver+sha256 в audit.

---

## 7. Concurrency & Locking

**Concurrency-модель — трёхуровневая:**

1. **`asyncio.Semaphore(MAX_CONCURRENT_CLI=4)`** — для capacity.
2. **In-memory `asyncio.Lock`** per resolved WIKI path — для correctness внутри процесса.
3. **On-disk `.wiki.lock`** (`fcntl.flock` advisory) — для durability через рестарты и совместимости с external writers.

**Acquire-порядок** — *строгий* (нарушение → deadlock):

1. Semaphore.
2. In-memory lock.
3. On-disk flock.

**Прочее:**

- **Stale-lock recovery** — по PID (`os.kill(pid, 0)`).
- **Atomic write** страниц через `tmp + os.replace`.
- Append-only `log.md` под тем же `.wiki.lock`.
- **External writers** (Obsidian) advisory-блокировка не останавливает, но сервис детектит вторжение по `mtime` и инструктирует CLI-агента через CLAUDE.md re-read.

**Решения:**

- [D-011](../decisions/D-011-concurrent-claude.md) — Semaphore(4) + per-WIKI in-memory Lock + asyncio.PriorityQueue (5 уровней, см. §3).
- [D-012](../decisions/D-012-wiki-lock.md) — on-disk `.wiki.lock` + atomic `tmp+os.replace` + stale recovery по PID.

---

## 8. Telegram I/O

Telegram-слой строится вокруг **aiogram 3.x** с asyncio.

**Voice / photo input:**

1. **Voice** — `faster-whisper` (CTranslate2, local CPU, ru/en, RTF≤0.5).
2. **Photo** — Claude Sonnet vision.
3. До routing оба файла сохраняются в `Inbox-WIKI/raw/media/_staging/<run_id>_<sha256[:8]>.<ext>`.
4. После Stage-1a resolution и confirm runtime *атомарно* переносит их в `<Domain-WIKI>/raw/media/<ISO8601>_<sha256[:8]>.<ext>` (immutable).
5. Если WIKI не нужен (`reminder_job`, rejected confirm, read-only query) — staging очищается retention-job'ом через **24h** после audit-записи.

**Confirmations** — **graduated**:

1. **Auto-confirm** — для тривиального read-only.
2. **Implicit ack** — для query/digest.
3. **Explicit confirm** — для writes/delete:
   - 3 inline-кнопки + free-form correction.
   - **TTL pending = 10мин** в `sessions.db`.

**Digest формат:**

- **HTML parse_mode** (`<b>`, `<i>`, `<a>`).
- *MarkdownV2 отвергнут* из-за escape-hell вложенных entity и хрупкости при динамической сборке.
- TL;DR-секцией первой.
- Actionable-кнопки только для items в окне ±2h.

**Output sizing:**

| Размер | Действие |
|--------|----------|
| ≤3500 chars | Одно сообщение |
| ≤10000 chars | Split на ≤3 части с `(N/M)` маркерами |
| >10000 chars | **Haiku-summary** (≤1500) + `send_document` `.md` |

- Full output **всегда** сохраняется в `<wiki>/data/runs/<YYYY-MM-DD>/<run_id>.md` с frontmatter.
- Индекс — в `audit.db.run_outputs`.

**Streaming:**

- Через `--output-format stream-json`.
- **Edit-throttle:** 1.5s / Δ50 chars.
- **Chain-split** на 4000 chars.
- **HTML-balancer** на каждом edit.
- **Final flush** в `finally`.

**Решения:**

- [D-022](../decisions/D-022-voice-photo-input.md) — `faster-whisper` STT + Claude vision, immutable `raw/media/`.
- [D-023](../decisions/D-023-tg-confirmations.md) — graduated confirmation.
- [D-024](../decisions/D-024-digest-format.md) — HTML parse_mode, TL;DR, actionable-cards только для ±2h.
- [D-025](../decisions/D-025-output-size.md) — threshold hybrid; always-persist на диск.
- [D-026](../decisions/D-026-tg-streaming.md) — hybrid edit + chain-split, throttle, final-flush guarantee.
- [D-032](../decisions/D-032-multi-language.md) — все системные строки `ru` hardcoded, без i18n catalog в MVP.
- [D-033](../decisions/D-033-chat-history.md) — `audit.db.chat_log` plaintext с 30d retention, last-20/24h окно для классификатора.

---

## 9. Auth, Admin, Onboarding

**Auth — single-tenant subscription:**

1. `claude auth login` выполняется *один раз* с `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`.
2. CLI scopes получают этот config dir **read-only** и тоже выставляют `CLAUDE_CONFIG_DIR`.
3. `ANTHROPIC_API_KEY` **не используется** для CLI.
4. Optional Stage-0 direct API backend — отдельный API credential через **systemd credentials / secret manager**, *не* `.env`, и *не реиспользует* Claude Code OAuth.

**Admin:**

- Семантически = user с расширенным scope.
- Единый `WORKSPACE_ROOT` walk.
- **`single` mode:** `admin == owner` без friction.
- **`multi` mode** (`TENANCY_MODE` config):
  1. Активирует `/admin elevate <USER>`.
  2. Временная **30-мин** сессия.
  3. Audit-события в `admin_events`.
  4. **Privacy boundary:** admin shadow получает failures, *не* контент.

**Allowlist:**

- **SSoT** — `users.toml` git-tracked.
- **Sync** в `sessions.db.users`:
  1. SIGHUP (primary).
  2. Watchdog (debounce 500ms, fallback).
  3. Validate-before-swap.
  4. Admin-alert на parse-error.
- `users.toml` использует canonical `telegram_id`; `telegram_username` и `display_name` *не являются* identity key.

**Конфиг-флаги** (ортогональны — допустимы все 4 комбинации):

- `TENANCY_MODE`: `single` | `multi`.
- `ENABLE_SELF_SIGNUP`: `bool`.

**Onboarding:**

1. `ENABLE_SELF_SIGNUP=false` — **default**.
2. При включении `/start` от unknown:
   1. Бот пишет полный D-042 user candidate в `pending_users`.
   2. Admin одобряет inline-кнопкой.
   3. Atomic append `users.toml` с `enabled=false` до completion.
   4. SIGHUP.
3. `USERS/<NAME>/` создаётся *только* после successful onboarding completion.
4. **Mandatory intro** «Что такое WIKI» перед Q&A — шаблон `templates/onboarding-intro.<lang>.md`.

**Lint-механика** (детализация 2026-05-10):

1. Каждый из 6 обязательных элементов помечен в шаблоне HTML-маркером: `<!-- INTRO_ELEMENT_ID:<slug> -->` сразу после соответствующего абзаца.
2. **Closed set** из 6 ID:
   1. `ai-library-concept`
   2. `no-lifecycle-commands`
   3. `preflight-clarification-and-duplicate-check`
   4. `explicit-confirm`
   5. `retention-30d-and-restore`
   6. `read-only-commands-list`
3. Lint-скрипт `scripts/lint_onboarding.py` (CI + pre-commit) парсит шаблон.
4. Отсутствие любого ID или дубль → **exit 1** с указанием отсутствующего slug'а.
5. Маркеры используются runtime'ом для measure-показа: бот логирует `intro_element_shown:<slug>` в `audit.db.onboarding_events` per user.

**Решения:**

- [D-013](../decisions/D-013-claude-cli-auth.md) — subscription mode, shared `CLAUDE_CONFIG_DIR`.
- [D-027](../decisions/D-027-anti-nesting-admin-boundary.md) — `WORKSPACE_ROOT` единый anchor.
- [D-028](../decisions/D-028-admin-access.md) — `TENANCY_MODE`, `/admin elevate` 30мин + audit trail.
- [D-030](../decisions/D-030-onboarding.md) — `users.toml` SSoT + admin approve flow + mandatory WIKI-intro.
- [D-031](../decisions/D-031-allowlist-hot-reload.md) — SIGHUP primary + watchdog fallback, validate-before-swap, soft-delete юзера.
- [D-042](../decisions/D-042-unify-user-config.md) — `users.toml` единый SSoT всех user-attributes; `roles.toml` упразднён.

---

## 10. Ops: deployment, logging, PII, git, testing, backup

### 10.1. Process isolation (D-038 уточнён)

**Бот:**

- Бежит под non-root system user `aisw-bot` (`/opt/ai-steward-wiki`, dedicated home).
- Unit-файл даёт *только* нужные capability:
  1. `AmbientCapabilities=CAP_SETUID CAP_SETGID`
  2. `CapabilityBoundingSet=CAP_SETUID CAP_SETGID`
  3. `NoNewPrivileges=false` на уровне бота.

**Каждый CLI-вызов:**

```
systemd-run --scope --uid=aisw-<N> --gid=aisw-<N> \
            --property=SupplementaryGroups=aisw-claude \
            --unit=cli-<job_id> --wait ...
```

- Флаг `--user` *не используется* — он выбирает user unit manager, а не target UID.

**Per-process лимиты transient scope:**

1. `MemoryMax=2G`
2. `TasksMax=64`
3. `CPUWeight=100`, `IOWeight=100`
4. `ProtectSystem=strict`
5. `ProtectHome=tmpfs`
6. `PrivateTmp=yes`, `PrivateDevices=yes`
7. `NoNewPrivileges=yes`
8. `ReadWritePaths=<wiki>`
9. `ReadOnlyPaths=<service>/prompts`
10. `ReadOnlyPaths=/var/lib/ai-steward-wiki/claude-code`
11. `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`

**Shared auth dir** — *намеренное* read-only исключение:

- CLI должен прочитать subscription auth, но path вне `--add-dir`.
- Runtime CLI: `--allowedTools "Read" "Write" "Edit" "Glob" "Grep"`.
- `--disallowedTools "Bash" "WebFetch" "Read(/var/lib/ai-steward-wiki/claude-code/**)"`.
- `--permission-mode dontAsk` — *не* позволяет fallback в prompt.

**Aggregate cap** — родительский `aisw-bot.slice`:

- `MemoryHigh=12G`, `MemoryMax=16G`, `TasksMax=512`.
- Гарантирует, что `4-конкурентные CLI × 2G + bot overhead` укладываются.

**STT / vision** — отдельная `aisw-stt.slice`:

- `MemoryMax=4G`, `CPUQuota=200%`.
- *Вне* CLI-scope'ов — чтобы не конкурировать за память Sonnet-процессов.

**Per-user UID** `aisw-<N>`:

- Создаётся idempotent через `systemd-sysusers` drop-in при первом WIKI юзера.
- Сохраняется в `users.toml`.

### 10.2. Backup (Q-E-36, MVP-partial — local safety net only)

**Граница MVP:**

- Реализуется минимальный *in-app local safety net* против software corruption / случайного удаления.
- **Disaster-recovery (HW-failure, off-site) — deferred.**
- D-037 §"Remote push" *явно запрещает* использовать git remote как backup (`git ≠ disaster-recovery`).
- WIKI content **не пушится** автоматически никуда — **local-only** до отдельного решения.

**Что входит в MVP:**

1. **State-DB snapshots:**
   1. Internal APScheduler maintenance job `db_snapshot` — ежедневно **03:00 UTC**.
   2. `VACUUM INTO state/snapshots/<UTC-date>/{jobs,audit,sessions}.db` (consistent SQLite hot-backup, без остановки WAL).
   3. *Silent system-maintenance task*, не user-facing `job-model`.
   4. **Local retention 7 дней** (rolling), `state/snapshots/` mode `0700`.
2. **WIKI content — local git history:**
   1. Per-WIKI git auto-commit ([D-037](../decisions/D-037-git-in-wiki.md)).
   2. Point-in-time recovery от bad Claude edit / случайной правки через `git revert` / `git checkout @{N}`.
   3. **Remote push не настраивается** (D-037 §"Remote push" п.1 — `No remote в MVP`).
   4. Opt-in per-WIKI remote — отдельным будущим решением.
3. **Restore-test** — manual checklist в `docs/runbook/restore.md`:
   1. Воспроизвести `db_snapshot`.
   2. Восстановить в `state-restore-test/`.
   3. Запустить `pytest tests/restore/` (smoke против snapshot'а).
   4. *Прогон обязателен перед каждым релизом.*

**Что MVP-partial backup НЕ покрывает** (явно):

- Аппаратный сбой VPS-диска (полная потеря всех `*-WIKI/` + `state/`).
- Ransomware.
- Физическое уничтожение хоста.

Эти классы — открытый риск, требуют отдельного решения (off-site target — `borg`/`restic`/`rclone-to-S3`/Backblaze) — за пределами MVP.

**Все deferred:**

- Off-site 3-2-1.
- `borg`/`restic`.
- GFS retention.
- Remote git push.

См. [backlog](../backlog.md#q-e-36--backup-wiki-и-state-db). **Триггер расширения:**

- Переход в `TENANCY_MODE=multi`.
- Размер state >1GB.
- Явный запрос юзера на off-site.

### 10.3. Logging

- **`structlog`** JSON-lines в stdout → journald.
- Стабильный набор полей: `ts`, `event`, `correlation_id`, `user_id`, `wiki_id`, `job_id`.
- Correlation context через `contextvars`.
- **PII-redactor processor** в pipeline.

### 10.4. PII tier-классификация (NIST SP 800-122)

1. **Tier-1 DROP** — tokens / passwords / keys / cards → `[REDACTED:tier1:...]`.
2. **Tier-2 MASK** — email / phone / IBAN — с shape preservation + hash для cross-ref.
3. **Tier-3 PLAINTEXT** — защищается per-store retention + unix-perm `0600`.

**Retention-таблица Tier-3** (явная разбивка по store'ам, refactor 2026-05-10):

| Store | Retention | Purge mechanism | Rationale |
|-------|-----------|-----------------|-----------|
| `audit.db.chat_log`        | **30d**  | APScheduler `chat_log_purge` daily 04:00 UTC | last-20/24h окно классификатора + GDPR-минимум (D-033) |
| `audit.db.tg_updates`      | **24h**  | APScheduler `tg_updates_purge` hourly        | dedup TTL, дольше не нужен |
| `audit.db.seen_files`      | **30d**  | APScheduler `seen_files_purge` daily         | content-hash dedup window |
| `audit.db.dedup_hits`      | **90d**  | APScheduler `dedup_hits_purge` daily         | outcome совпадений content-hash (D-018) — forensics window выровнен с `audit_events` |
| `audit.db.audit_events`    | **90d**  | APScheduler `audit_purge` daily              | audit/forensics minimum по NIST SP 800-92 |
| `audit.db.admin_events`    | **90d**  | то же                                        | admin-elevate trail |
| `audit.db.job_outputs`     | **90d**  | APScheduler `job_outputs_purge` daily        | routing/delivery metadata для cron-результатов (D-020) — сохраняем в окне audit |
| `audit.db.run_outputs`     | **180d** | APScheduler `run_outputs_purge` daily        | индекс full-output файлов в `<wiki>/data/runs/` (D-025); content indefinite, индекс — bounded |
| `audit.db.prompt_versions` | *indefinite* | no auto-GC | semver+sha256 системных промптов (D-015); компактен, нужен для full-replay любого исторического run |
| `audit.db.onboarding_events` | **180d** | APScheduler `onboarding_purge` daily         | measure-показ intro-элементов per user (D-030) — нужно для повторных onboarding/audit |
| `sessions.db.users`        | live     | заменяется sync'ом из `users.toml`           | snapshot allowlist (D-031/D-042); retention = жизнь сервиса |
| `sessions.db.pending_users` | **14d** | APScheduler `pending_users_purge` daily      | заявки `/start` до admin-approve (D-030); просроченные удаляются |
| `sessions.db.pending_confirms` | **10мин TTL** | inline на каждый confirm-cycle           | explicit-confirm window (D-023) |
| `sessions.db.inbox_hint_cache` | live  | regen on cache-miss / service-restart        | runtime-каталог Inbox hint (см. §4); не PII, не имеет TTL |
| `sessions.db.fsm`          | live     | aiogram FSM storage; cleared on dialog completion | runtime conversation state |
| `jobs.db.tracker_answers`  | **90d**  | APScheduler `tracker_purge` daily            | top-3 prediction window (D-014) |
| `Inbox-WIKI/raw/media/_staging/` | **24h** | APScheduler `staging_purge` hourly       | unrouted voice/photo upload (D-022, см. §8) |
| `state/snapshots/`         | **7d**   | inline в `db_snapshot` job (rolling)         | backup safety net |
| `<wiki>/data/runs/`        | *indefinite* | no auto-GC in MVP; future retention — separate decision | full-output replay/audit invariant (D-025) |
| `<wiki>/raw/media/`        | *indefinite* | manual via NL `purge_wiki` / admin GDPR purge | user-uploaded, immutable per D-022 |
| `_trash/<Domain>-WIKI-<ts>` | **30d** | APScheduler `trash_purge` daily              | soft-delete restore window (D-041, см. §5) |

**At-rest crypto** — *не* вводится в MVP (single-tenant). Триггер пересмотра:

- Переход в `TENANCY_MODE=multi` с реальными независимыми юзерами.
- Появление Tier-1/Tier-2 PII вне redactor-контура.

### 10.5. Git per-WIKI

1. `git init` на materialize.
2. Auto-commit после успешного PostRun-write с форматом `<job_id>(<category>): <title>`.
3. `gitleaks` pre-commit hook.
4. `.wiki.lock` и `data/runs/` — в `.gitignore`.

### 10.6. Тестирование (трёхуровневое)

1. **Unit** — с `FakeClaudeRunner` Protocol (coverage **80% core**).
2. **Integration** — `RUN_INTEGRATION=1` с реальным CLI subscription **nightly**.
3. **Manual E2E** — checklist перед релизом.

### 10.7. Log-даты

- **Runtime WIKI `log.md`** — ISO 8601 с TZ-offset до минуты:
  - Формат: `## [YYYY-MM-DDTHH:MM±HH:MM] <op> | <title>`.
  - TZ из per-WIKI frontmatter override → user-default → `Europe/Moscow`.
- **Машинные timestamp'ы** (audit / chat_log / tracker) — **UTC с миллисекундами**.

**Решения:**

- [D-034](../decisions/D-034-pii-redactor.md) — Tier-1/2/3 retention; write-time hook; GDPR-purge через admin.
- [D-035](../decisions/D-035-service-logging.md) — `structlog` JSON-lines → journald, correlation_id через contextvars.
- [D-036](../decisions/D-036-testing-strategy.md) — unit + integration nightly + manual E2E.
- [D-037](../decisions/D-037-git-in-wiki.md) — git init per-WIKI, auto-commit, gitleaks, media в .gitignore.
- [D-038](../decisions/D-038-per-user-systemd.md) — `systemd-run --scope` per CLI, RO auth, no-Bash, per-process limits, slices.
- [D-040](../decisions/D-040-log-date-format.md) — runtime ISO 8601 + TZ-offset; машинные UTC мс.
- **Q-E-36 / backup (MVP-partial):** `db_snapshot` + per-WIKI git history (local) + restore-test runbook. Off-site / GFS / remote — deferred.

---

## 11. CLAUDE.md Templates

**Расположение:** `ai-steward-wiki/templates/<type>.md`.

**Доменные пресеты:**

- `health`, `health-lite`, `investment`, `budget`, `family`, `study`, `career`, `home`, `hobby`, `recipes`.
- `_default.md` — fallback.

**Источник истины:**

1. Шаблоны — **локальная SSoT** этого репозитория.
2. **Никакой live-sync** с parent-`CLAUDE.md` сервиса `ai-steward` (TG-бот).
3. Это нарушило бы границу изоляции (Spec-WIKI/CLAUDE.md §1.1).
4. Один раз, при initial bootstrap репо, доменные знания *могут* быть скопированы из любого внешнего источника (включая parent ai-steward) как стартовая инспирация.
5. После — шаблоны эволюционируют *только* PR'ами в этот репозиторий.

**D-017 amended 2026-05-10:**

- Runtime-чтение parent-ai-steward — **запрещено**.
- Template lookup поддерживает primary slug (`healthlite`) и hyphenated alias (`health-lite`) без расширения D-008 regex для WIKI-директорий.

**Workflow «давай заведём вики для X»** (post D-041):

1. Router нормализует имя.
2. Lookup пресета.
3. Копирует с placeholder-substitution.
4. Создаёт стандартную структуру: `entities/`, `concepts/`, `raw/`, `index.md`, `log.md`.

**Inbox `CLAUDE.md`** — отдельный shared template в `templates/inbox-wiki/`:

- Содержит router-логику, intent vocabulary и universal pre-flight.
- *Без* списка доменов — router читает их runtime'ом из `## Inbox hint` каждой Domain-WIKI.

**Каждый сгенерированный `CLAUDE.md` обязан содержать:**

1. Секцию `## Inbox hint` (1–3 строки, lint-checkable).
2. YAML frontmatter версионирования (D-039) для evolution через linear migration chain.

**Решения:**

- [D-016](../decisions/D-016-inbox-claude-md-template.md) — Inbox-template с intent vocabulary + universal pre-flight; per-domain `## Inbox hint`.
- [D-017](../decisions/D-017-domain-claude-md-template.md) — per-domain пресеты + `_default.md`; локальная SSoT `templates/`.
- [D-039](../decisions/D-039-claude-md-evolution.md) — frontmatter + managed/user-zone + linear migration chain `v1→v2→…`.

---

## 12. Coverage map & open backlog

**Overview §9** содержит **39 вопросов**:

- Tier A: 1–8
- Tier B: 9–19
- Tier C: 20–24
- Tier D: 25–30
- Tier E: 31–39

**Из 41 active D-файлов:**

1. **38** — прямые ответы на Q1–Q39 (1:1).
2. **3** — derived/meta:
   - `D-001` — time-tracker как надстройка над job-model для Q1.
   - `D-014` — tracker-memory как sub-design над `D-001`.
   - `D-042` — `users.toml` unify как refactor над Q5/Q27/Q28.

**Superseded:** `D-029 → D-041` (оба покрывают Q26).

**Прямой mapping Q→D** (полная таблица):

| Tier | Q | Тема | D | Tier | Q | Тема | D |
|------|---|------|---|------|---|------|---|
| A | 1  | job-model               | D-002 | C | 20 | CLI auth            | D-013 |
| A | 2  | scheduler backend       | D-003 | C | 21 | system-prompt inject| D-015 |
| A | 3  | inbox per-user/global   | D-004 | C | 22 | `--add-dir` scope   | D-007 |
| A | 4  | classifier engine       | D-009 | C | 23 | WIKI-marker regex   | D-008 |
| A | 5  | NL time-parsing         | D-010 | C | 24 | anti-nesting admin  | D-027 |
| A | 6  | planner.json SSoT       | D-005 | D | 25 | admin access        | D-028 |
| A | 7  | concurrent Claude       | D-011 | D | 26 | wiki-lifecycle auth | D-029→**D-041** |
| A | 8  | wiki-lock               | D-012 | D | 27 | onboarding          | D-030 |
| B | 9  | inbox CLAUDE.md         | D-016 | D | 28 | allowlist reload    | D-031 |
| B | 10 | domain CLAUDE.md        | D-017 | D | 29 | multi-language      | D-032 |
| B | 11 | ingest idempotency      | D-018 | D | 30 | chat history        | D-033 |
| B | 12 | TG confirmations        | D-023 | E | 31 | per-user systemd    | D-038 |
| B | 13 | digest format           | D-024 | E | 32 | state storage       | D-006 |
| B | 14 | voice/photo input       | D-022 | E | 33 | audit PII           | D-034 |
| B | 15 | streaming               | D-026 | E | 34 | service logging     | D-035 |
| B | 16 | output size             | D-025 | E | 35 | testing strategy    | D-036 |
| B | 17 | timeouts/kill           | D-021 | E | 36 | backup              | **MVP-partial / deferred-full** (см. §10, [backlog](../backlog.md#q-e-36--backup-wiki-и-state-db)) |
| B | 18 | cron result routing     | D-020 | E | 37 | git in WIKI         | D-037 |
| B | 19 | cron failure mode       | D-019 | E | 38 | CLAUDE.md evolution | D-039 |
|   |    |                         |       | E | 39 | log date format     | D-040 |

**Статус покрытия:**

- ✅ **Закрыто полностью:** 38/39.
- ⚠️ **Q36** — покрыт частично:
  - В MVP: state-DB snapshots + local per-WIKI git history.
  - **Deferred:** git remote push, 3-2-1 / GFS / borg-restic.
