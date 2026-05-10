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

> Сводное техническое описание, агрегирующее 42 артефакта-решения (D-001…D-042): 41 active + 1 superseded (D-029 → D-041). Источник истины по архитектурным выборам — соответствующие `decisions/D-NNN-*.md`. Этот документ — навигационная карта, не SSoT.

## 1. Executive Summary & Scope

`ai-steward-wiki` — изолированный мультипользовательский Telegram-сервис на отдельной VPS, который превращает Claude Code CLI в персонального WIKI-ассистента в стиле метода Карпаты. Юзер общается с ботом естественным языком (текст, файл, фото, голос), без явного указания папок и команд: бот идентифицирует автора по `telegram_id`, классифицирует контент через Stage-0 Haiku → Stage-1a/1b Sonnet pipeline и запускает Claude в нужной `<Domain>-WIKI/`. Сервис **полностью изолирован** от существующего TG-бота `ai-steward` (см. CLAUDE.md §1.1): нет общего volume, нет миграции `planner.json`, нет cross-service чтений, нулевая связь по умолчанию. MVP-объём — single-tenant Henry-N, подписочный auth, классификатор + scheduler + per-WIKI lifecycle через NL-промпты, voice/photo input, streaming-ответы в TG.

Решения этого слоя:

- [overview §1–§1.1](../raw/20260507-ai-steward-wiki-only-overview.md) — назначение, граница с `ai-steward`, базовый поток.
- [D-007](../decisions/D-007-add-dir-scope.md) — `--add-dir` scope: per-WIKI workspace, auto-walk `USERS/<USER>/CLAUDE.md` для профиля.
- [D-013](../decisions/D-013-claude-cli-auth.md) — single-tenant subscription mode, все юзеры = один реальный человек.
- [D-027](../decisions/D-027-anti-nesting-admin-boundary.md) — `WORKSPACE_ROOT` как единый anchor всей файловой иерархии.
- [D-041](../decisions/D-041-no-direct-wiki-commands.md) — lifecycle WIKI только через NL-промпт, прямых команд нет.

## 2. Data Model & Storage

State сервиса разнесён по трём SQLite-БД с разными bounded contexts: горячая операционная `jobs.db`, append-only `audit.db`, runtime-state `sessions.db`. Унифицированная таблица `jobs` использует Flat + typed JSON payload — общие колонки (`id, kind, owner_telegram_id, cron_expr, enabled, mandatory, follow_up_delay_min, ...`) индексируются, а kind-специфика валидируется Pydantic discriminated union на boundary без миграций на каждый новый kind. Identity vocabulary из [D-042](../decisions/D-042-unify-user-config.md): `telegram_id` — canonical external user id; `owner_telegram_id` допустим только как `jobs` owner column; `chat_id` — только delivery target; `user_id` — только internal DB surrogate. Tracker-память — append-only таблица в `jobs.db`, top-3 предсказания вычисляются on-the-fly SQL-запросом по 90-дневному окну. `audit.db` также держит `job_outputs` и `run_outputs`, чтобы routing/delivery metadata не терялась между D-020/D-025 и storage map. Все БД работают в WAL+NORMAL+foreign_keys с `busy_timeout=5000`; миграции — Alembic per-DB.

- [D-002](../decisions/D-002-job-model-storage.md) — `jobs` = одна таблица, общие колонки + JSON `payload`, валидация Pydantic.
- [D-006](../decisions/D-006-state-storage-layout.md) — три раздельных SQLite (`jobs.db`, `audit.db`, `sessions.db`), Alembic per-DB, WAL.
- [D-014](../decisions/D-014-tracker-memory-model.md) — `tracker_answers` append-only в `jobs.db`, top-3 on-the-fly SQL, retention 90д.

## 3. Job / Scheduler Core

Scheduler-ядро = APScheduler `AsyncIOScheduler` + `SQLAlchemyJobStore` в том же процессе бота, persistence в `jobs.db` через общий engine. APScheduler не запускает CLI напрямую — он `put`'ит задачу в `asyncio.PriorityQueue` с 5 уровнями приоритета (`interactive=0`, `user_write=1`, `cron_write=2`, `digest=3`, `ingest=4`), откуда worker'ы тянут под `Semaphore(MAX_CONCURRENT_CLI=4)` и per-WIKI lock'ом. Failure-handling — taxonomy Transient/Permanent/Unknown с exp.backoff retry (override per-category в payload), DLQ-таблица с manual `/retry`, auto-disable recurring job'а после 3 подряд провалов. Timeout'ы — per-category hard limits (Haiku 15s, wiki_job 120s, digest 600s — каноническая таблица в [D-021](../decisions/D-021-timeouts-kill-policy.md)) с graceful kill-sequence SIGTERM→10s→SIGKILL и UX-cancel через inline-кнопку.

- [D-001](../decisions/D-001-time-tracker-vs-job-model.md) — time-tracker = надстройка над `job-model`, не отдельная подсистема.
- [D-003](../decisions/D-003-scheduler-backend.md) — APScheduler `AsyncIOScheduler` + `SQLAlchemyJobStore` in-process, общий engine с `jobs.db`.
- [D-019](../decisions/D-019-cron-failure-mode.md) — error taxonomy + retry exp.backoff + DLQ + auto-disable после 3 подряд fail.
- [D-020](../decisions/D-020-cron-result-routing.md) — `notify_policy` per-job (`always|on_output|silent`) + admin shadow channel.
- [D-021](../decisions/D-021-timeouts-kill-policy.md) — per-category hard timeouts + SIGTERM→10s→SIGKILL + UX `/cancel`.

## 4. Inbox & Routing

Inbox-WIKI лежит per-user в `USERS/<NAME>/Inbox-WIKI/`, `CLAUDE.md` рендерится из shared template в репо при materialize. Каждая `<Domain>-WIKI/CLAUDE.md` обязана содержать секцию `## Inbox hint`, которую **router-Sonnet (Stage-1a, см. §6 терминологию)** читает для построения runtime-каталога доменов; сам Inbox-`CLAUDE.md` доменов не знает, только мета-правила, intent-таксономия и universal pre-flight. **Hint-cache:** runtime-каталог доменов кэшируется в `sessions.db.inbox_hint_cache` per-user, но не материализуется в WIKI-файлы. Cache-ключ — `(user_id, wiki_path)`, где `user_id` — internal sessions surrogate; metadata guards — `size_bytes`, `mtime_ns`, `ctime_ns`; truth guard — `content_sha256`. Hot-path: `stat(CLAUDE.md)` совпал по всем metadata guards → cache hit без чтения файла. На metadata mismatch — read+sha256; если `sha256 == cache.content_sha256`, обновить только metadata guards (touch/rsync без re-parse), иначе re-parse `## Inbox hint` секции (regex `^## Inbox hint\s*$` до следующего `^## ` или EOF) → atomic swap (`size_bytes, mtime_ns, ctime_ns, content_sha256, hint_text`). `ctime_ns` закрывает preserved-mtime сценарии: пользователь может сохранить `mtime`, но не может вручную выставить Linux `ctime`. Без cache — N×file-read + N×3500 tokens на каждое сообщение при 20 WIKI; с cache — 0 чтений в hot-path, ~80% TTFB-экономия. Idempotency двухслойная без LLM, оба слоя — в `audit.db` (общий bounded context observability/dedup, [D-006](../decisions/D-006-state-storage-layout.md), [D-018](../decisions/D-018-ingest-idempotency.md)): L1 TG-update dedup `audit.db.tg_updates` (24h TTL), L2 content-hash SHA-256 `audit.db.seen_files` (30d TTL) с inline-confirm на коллизии. Прямых lifecycle-команд для WIKI больше нет — все важные операции (create/delete/restore/rename/merge/split/edit-rules/edit-persona/purge + page-level) проходят 5-шаговый pre-flight (см. [D-041](../decisions/D-041-no-direct-wiki-commands.md)): intent-grounding → blind-spot scan → clarification → confirm → execute+audit.

- [D-004](../decisions/D-004-inbox-wiki-scope.md) — Inbox per-user + shared template, materialize at init.
- [D-016](../decisions/D-016-inbox-claude-md-template.md) — Inbox hint в каждой Domain-WIKI, closed intent vocabulary, universal pre-flight.
- [D-018](../decisions/D-018-ingest-idempotency.md) — двухслойный dedup без LLM (L1 TG update_id, L2 content SHA-256); оба слоя в `audit.db` (D-018 amended 2026-05-10).
- [D-041](../decisions/D-041-no-direct-wiki-commands.md) — нет `/wiki_init|delete|restore|purge`, только NL-промпт + read-only `/wiki_list`,`/wiki_show`.

## 5. Wiki Lifecycle

Жизненный цикл WIKI-папки управляется через NL-промпты в Inbox-WIKI router'у с обязательным graduated explicit confirm для destructive операций. **Нормализация имени:** свободное имя из NL-промпта (`health`, `Health Lite`, `health-lite`, `здоровье`) проходит pipeline: (1) транслитерация Cyrillic → Latin (single-strategy ISO 9), (2) split по non-alphanumeric, (3) PascalCase join (`health lite` → `HealthLite`), (4) suffix `-WIKI` (`HealthLite-WIKI`), (5) валидация regex `^[A-Z][A-Za-z0-9]*-WIKI$` (D-008). Lookup пресета (§11) — case-insensitive по slug `healthlite` ИЛИ по `health-lite`-варианту с восстановленным дефисом для имени файла шаблона; если `templates/healthlite.md` отсутствует и `templates/health-lite.md` существует — используется он, иначе `_default.md`. Mapping `slug → wiki-name` логируется в `audit.db` на create. Soft-delete переносит папку в `USERS/<NAME>/_trash/<Domain>-WIKI-<ts>/` с trash-retention 30 дней (отдельный механизм от PII tier-3 retention в §10, но **PII-redactor проходит и по `_trash/`** на soft-delete trigger — финальный tier-1 DROP / tier-2 MASK sweep, см. [D-034](../decisions/D-034-pii-redactor.md) §"Trash sweep"; tier-3 plaintext остаётся под общей retention 30d, по истечении — hard-delete с `shred -u` для media и `unlink` для md, audit-event `trash_purged`) и возможностью восстановления NL-промптом (intent `restore_wiki`, см. [D-041](../decisions/D-041-no-direct-wiki-commands.md)); `_trash/` исключается из autodiscover, soft limit (20 WIKI/user) и anti-nesting walk. Имя WIKI валидируется regex `^[A-Z][A-Za-z0-9]*-WIKI$` (strict whitelist) — единая convention WIKI-маркера, используется autodiscover, anti-nesting walk и lifecycle-операциями. Soft-limit: 20 WIKI/user (warn at 16, hard reject at 20, `_trash/` не учитывается). Каждый сгенерированный `CLAUDE.md` несёт YAML frontmatter (`schema_version`, `template_id`, `last_migrated_at`, `template_sha256`) и разделён на managed/user-zone секции через HTML-комменты — миграции применяют 3-way merge (declarative) или imperative `vN_to_vM.py` для нелинейных изменений.

- [D-005](../decisions/D-005-no-planner-json.md) — `planner.json` не существует нигде в дереве сервиса; единственный SSoT — `jobs.db`.
- [D-008](../decisions/D-008-wiki-marker-format.md) — regex `^[A-Z][A-Za-z0-9]*-WIKI$` как единое правило WIKI-маркера.
- [D-039](../decisions/D-039-claude-md-evolution.md) — frontmatter + managed/user-zone секции + linear migration chain.
- *History:* [D-029](../decisions/D-029-wiki-init-auth.md) — исходный CRUD-design `/wiki_init|delete|restore|purge`, **полностью superseded D-041**. Числовые механизмы (soft-limit 20 / warn at 16 / hard reject 20, Levenshtein ≤2, `_trash/` retention 30d, `_trash/` exclusion из autodiscover/counter/anti-nesting) явно зафиксированы как новый SSoT в [D-041](../decisions/D-041-no-direct-wiki-commands.md) §"Anti-spam защита" — не наследуются неявно из superseded артефакта.

## 6. Classifier & NLP

Classifier — трёхступенчатый pipeline с **disambiguated терминологией** (refactor 2026-05-10):

1. **Stage-0 = classifier-Haiku.** Default backend в subscription-only MVP — headless Claude CLI Haiku (`claude --model claude-haiku-4-5 --output-format json --json-schema <classifier-json-schema> --max-turns 1 --disallowedTools "Bash" "Read" "Write" "Edit" "Glob" "Grep" "WebFetch" --permission-mode dontAsk`) с shared Claude Code auth из [D-013](../decisions/D-013-claude-cli-auth.md). Optional direct Anthropic SDK/API backend разрешён только при отдельном `STAGE0_BACKEND=anthropic_api` и отдельном API credential, не из Claude Code OAuth. Возвращает `{intent, confidence, distilled_payload}`.
2. **Stage-1a = router-Sonnet (Inbox-context).** `claude --model sonnet -p ... --add-dir <Inbox-WIKI>` с подгруженным runtime-каталогом доменов из hint-cache (см. §4). Возвращает целевую `<Domain>-WIKI` + final intent после pre-flight clarification.
3. **Stage-1b = executor-Sonnet (Domain-context).** `claude --model sonnet -p ... --add-dir <Domain-WIKI>` с domain-CLAUDE.md и Karpathy-режимом (ingest/query/lint). Производит side-effects на содержимое WIKI.

Fast-path: `intent=reminder & confidence ≥ 0.85 & время распарсено` → прямая запись в `jobs` (`kind='reminder_job'`) без Stage-1a/1b. Reminder-job не привязан к WIKI: `owner_telegram_id` берётся из TG-update, `wiki_id` payload-поле = `null`, при срабатывании firing-handler доставляет TG-message напрямую без `<Domain>-WIKI` workspace и без CLI-вызова. Это сознательная asymmetry: `reminder_job` — pure scheduler→TG, `wiki_job`/`ingest_job`/`digest_job` требуют resolved WIKI и проходят полный pipeline. Иначе Stage-0 → Stage-1a → (опц.) Stage-1b. NL-парсинг времени двухступенчатый: сначала `dateparser` (rule-based, ~50ms, ru/en, user-TZ из `users.toml` per D-042), на промах — Haiku-fallback с узким system-промптом, на ambiguous — эскалация в Stage-1a «уточни время». Все datetime в `jobs.db` хранятся в UTC, user-TZ применяется только на input/output. System-prompt inject — Hybrid: `prompts/classifier.md` backend-independent для Stage-0; default CLI backend передаёт его через `--append-system-prompt @file`, optional API backend — как SDK/API system instructions. `prompts/wiki.md` передаётся CLI для всех Stage-1a/1b через `--append-system-prompt @file`; `prompts/inbox.md` добавляется к Stage-1a; `prompts/domain-<type>.md` добавляется к Stage-1b. Semver+sha256 всех prompt-файлов логируются в `audit.db.prompt_versions`.

- [D-009](../decisions/D-009-classifier-engine.md) — Stage-0 Haiku (CLI default, API optional) → Stage-1 CLI Sonnet (action), threshold 0.85.
- [D-010](../decisions/D-010-nl-time-parsing.md) — `dateparser` → Haiku fallback → Stage-1 escalation; UTC storage, user-TZ обязателен.
- [D-015](../decisions/D-015-system-prompt-inject.md) — Hybrid `wiki.md` + `inbox.md` + `classifier.md`; backend-independent classifier prompt для Stage-0, CLI `--append-system-prompt @file` для Stage-1, semver+sha256 в audit.

## 7. Concurrency & Locking

Concurrency-модель трёхуровневая: `asyncio.Semaphore(MAX_CONCURRENT_CLI=4)` для capacity, in-memory `asyncio.Lock` per resolved WIKI path для correctness внутри процесса, on-disk `.wiki.lock` (`fcntl.flock` advisory) для durability через рестарты и совместимости с external writers. Acquire-порядок строгий: semaphore → in-memory lock → on-disk flock; нарушение порядка ведёт к deadlock'у. Stale-lock recovery — по PID (`os.kill(pid, 0)`), atomic write страниц через `tmp + os.replace`, append-only `log.md` под тем же `.wiki.lock`. External writers (Obsidian) advisory-блокировка не останавливает, но сервис детектит вторжение по `mtime` и инструктирует CLI-агента через CLAUDE.md re-read.

- [D-011](../decisions/D-011-concurrent-claude.md) — Semaphore(4) + per-WIKI in-memory Lock + asyncio.PriorityQueue (5 уровней, см. §3).
- [D-012](../decisions/D-012-wiki-lock.md) — on-disk `.wiki.lock` (flock advisory) + atomic `tmp+os.replace` + stale recovery по PID.

## 8. Telegram I/O

Telegram-слой строится вокруг aiogram 3.x с asyncio. Voice — `faster-whisper` (CTranslate2, local CPU, ru/en, RTF≤0.5), photo — Claude Sonnet vision. До routing оба файла сохраняются в `Inbox-WIKI/raw/media/_staging/<run_id>_<sha256[:8]>.<ext>`; после Stage-1a resolution и confirm runtime атомарно переносит их в `<Domain-WIKI>/raw/media/<ISO8601>_<sha256[:8]>.<ext>` immutable. Если WIKI не нужен (`reminder_job`, rejected confirm, read-only query), staging очищается retention-job'ом через 24h после audit-записи. Confirmations — graduated: auto-confirm для тривиального read-only, implicit ack для query/digest, explicit confirm с 3 inline-кнопками + free-form correction для writes/delete (TTL pending=10мин в `sessions.db`). Digest формат — HTML parse_mode (`<b>`, `<i>`, `<a>`; MarkdownV2 отвергнут из-за escape-hell вложенных entity и хрупкости при динамической сборке), TL;DR-секцией первой, actionable-кнопки только для items в окне ±2h. Output sizing: ≤3500 chars одним сообщением, ≤10000 — split на ≤3 части с `(N/M)` маркерами, >10000 — Haiku-summary (≤1500) + `send_document` `.md`; full output всегда сохраняется в `<wiki>/data/runs/<YYYY-MM-DD>/<run_id>.md` с frontmatter и индексом в `audit.db.run_outputs`. Streaming через `--output-format stream-json` с edit-throttle 1.5s/Δ50chars, chain-split на 4000 chars, HTML-balancer на каждом edit, final flush в `finally`.

- [D-022](../decisions/D-022-voice-photo-input.md) — `faster-whisper` для STT + Claude vision для photo, immutable `raw/media/`.
- [D-023](../decisions/D-023-tg-confirmations.md) — graduated confirmation: auto / implicit ack / explicit (3 кнопки + free-form).
- [D-024](../decisions/D-024-digest-format.md) — HTML parse_mode, TL;DR-секция, actionable-cards только для ±2h items.
- [D-025](../decisions/D-025-output-size.md) — threshold hybrid: ≤3500 inline / ≤10000 split / >10000 summary+document; always-persist на диск.
- [D-026](../decisions/D-026-tg-streaming.md) — hybrid edit + chain-split на 4000, throttle 1.5s, final-flush guarantee.
- [D-032](../decisions/D-032-multi-language.md) — все системные строки `ru` hardcoded, без i18n catalog в MVP.
- [D-033](../decisions/D-033-chat-history.md) — `audit.db.chat_log` plaintext с 30d retention, last-20/24h окно для классификатора.

## 9. Auth, Admin, Onboarding

Auth — single-tenant subscription: `claude auth login` выполняется один раз с `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`; CLI scopes получают этот config dir read-only и тоже выставляют `CLAUDE_CONFIG_DIR`. `ANTHROPIC_API_KEY` не используется для CLI; optional Stage-0 direct API backend требует отдельный API credential через systemd credentials / secret manager, не `.env`, и не реиспользует Claude Code OAuth. Admin семантически = user с расширенным scope, единый `WORKSPACE_ROOT` walk, в `single` mode admin==owner без friction; `multi` mode (TENANCY_MODE config) активирует `/admin elevate <USER>` с временной 30-мин сессией, audit-событиями в `admin_events` и privacy boundary (admin shadow получает failures, не контент). Allowlist SSoT — `users.toml` git-tracked, sync в `sessions.db.users` через SIGHUP (primary) + watchdog (debounce 500ms, fallback) с validate-before-swap и admin-alert на parse-error. `users.toml` использует canonical `telegram_id`; `telegram_username` и `display_name` не являются identity key. `TENANCY_MODE` (single|multi) и `ENABLE_SELF_SIGNUP` (bool) ортогональны: допустимы все 4 комбинации (single+self-signup, multi+admin-only и т.д.). Onboarding — `ENABLE_SELF_SIGNUP=false` default; при включении `/start` от unknown пишет полный D-042 user candidate в `pending_users`, admin одобряет inline-кнопкой → atomic append `users.toml` с `enabled=false` до completion → SIGHUP. `USERS/<NAME>/` создаётся только после successful onboarding completion. Mandatory intro «Что такое WIKI» перед Q&A — шаблон `templates/onboarding-intro.<lang>.md`. **Lint-механика (детализация 2026-05-10):** каждый из 6 обязательных элементов помечен в шаблоне HTML-маркером вида `<!-- INTRO_ELEMENT_ID:<slug> -->` сразу после соответствующего абзаца. Lint-скрипт `scripts/lint_onboarding.py` (CI + pre-commit) парсит шаблон и проверяет наличие всех 6 ID из closed set: `ai-library-concept`, `no-lifecycle-commands`, `preflight-clarification-and-duplicate-check`, `explicit-confirm`, `retention-30d-and-restore`, `read-only-commands-list`. Отсутствие любого ID или дубль → exit 1 с указанием отсутствующего slug'а. Маркеры также используются runtime'ом для measure-показа: бот логирует `intro_element_shown:<slug>` в `audit.db.onboarding_events` per user.

- [D-013](../decisions/D-013-claude-cli-auth.md) — subscription mode, shared `CLAUDE_CONFIG_DIR`, CLI auth отдельно от optional Stage-0 API credential.
- [D-027](../decisions/D-027-anti-nesting-admin-boundary.md) — `WORKSPACE_ROOT` единый anchor, admin = user с расширенным scope.
- [D-028](../decisions/D-028-admin-access.md) — TENANCY_MODE single|multi, `/admin elevate` с 30мин сессией и audit trail.
- [D-030](../decisions/D-030-onboarding.md) — `users.toml` SSoT + admin approve flow + mandatory WIKI-intro перед Q&A.
- [D-031](../decisions/D-031-allowlist-hot-reload.md) — SIGHUP primary + watchdog fallback, validate-before-swap, soft-delete юзера.
- [D-042](../decisions/D-042-unify-user-config.md) — `users.toml` единый SSoT всех user-attributes (`telegram_id`, role, lang, timezone, persona, unix UID fields); `roles.toml` упразднён.

## 10. Ops: deployment, logging, PII, git, testing, backup

**Process isolation (D-038 уточнён).** Бот бежит под non-root system user `aisw-bot` (`/opt/ai-steward-wiki`, dedicated home). Unit-файл даёт только нужные capability: `AmbientCapabilities=CAP_SETUID CAP_SETGID`, `CapabilityBoundingSet=CAP_SETUID CAP_SETGID`, `NoNewPrivileges=false` на уровне бота. Каждый CLI-вызов запускается системным `systemd-run --scope --uid=aisw-<N> --gid=aisw-<N> --property=SupplementaryGroups=aisw-claude --unit=cli-<job_id> --wait ...`; флаг `--user` не используется, потому что он выбирает user unit manager, а не target UID. Transient scope получает **per-process** лимиты `MemoryMax=2G`, `TasksMax=64`, `CPUWeight=100`, `IOWeight=100`, `ProtectSystem=strict`, `ProtectHome=tmpfs`, `PrivateTmp=yes`, `PrivateDevices=yes`, `NoNewPrivileges=yes`, `ReadWritePaths=<wiki>`, `ReadOnlyPaths=<service>/prompts`, `ReadOnlyPaths=/var/lib/ai-steward-wiki/claude-code`, `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`. Shared auth dir — намеренное read-only исключение: CLI должен прочитать subscription auth, но path вне `--add-dir`; runtime CLI использует `--allowedTools "Read" "Write" "Edit" "Glob" "Grep"` и `--disallowedTools "Bash" "WebFetch" "Read(/var/lib/ai-steward-wiki/claude-code/**)"`; `--permission-mode dontAsk` не позволяет fallback в prompt. Aggregate cap — родительский `aisw-bot.slice` с `MemoryHigh=12G MemoryMax=16G TasksMax=512`, гарантирует, что 4-конкурентные CLI (Semaphore из D-011) × 2G + bot overhead укладываются. STT (`faster-whisper`, CPU-bound) и vision pre-processing бегут в отдельной `aisw-stt.slice` (`MemoryMax=4G`, `CPUQuota=200%`) **вне** CLI-scope'ов, чтобы не конкурировать за память Sonnet-процессов. Per-user UID `aisw-<N>` создаётся idempotent через `systemd-sysusers` drop-in при первом WIKI юзера и сохраняется в `users.toml`.

**Backup (Q-E-36, MVP-partial — local safety net only).** В MVP реализуется минимальный in-app local safety net против software corruption / случайного удаления; **disaster-recovery (HW-failure, off-site) остаётся deferred**. Граница задана осознанно: D-037 §"Remote push" явно запрещает использовать git remote как backup (`git ≠ disaster-recovery`), поэтому WIKI content **не пушится** автоматически никуда — local-only до отдельного решения.

1. **State-DB snapshots.** Internal APScheduler maintenance job `db_snapshot` ежедневно 03:00 UTC выполняет `VACUUM INTO state/snapshots/<UTC-date>/{jobs,audit,sessions}.db` (consistent SQLite hot-backup, без остановки WAL). Это silent system-maintenance task, не user-facing `job-model`. Local retention 7 дней (rolling), `state/snapshots/` mode 0700.
2. **WIKI content — local git history.** Per-WIKI git auto-commit ([D-037](../decisions/D-037-git-in-wiki.md)) обеспечивает point-in-time recovery от bad Claude edit / случайной правки через `git revert` / `git checkout @{N}`. **Remote push не настраивается** (D-037 §"Remote push" п.1 — `No remote в MVP`). Opt-in per-WIKI remote — отдельным будущим решением, не в этой spec'е.
3. **Restore-test.** Manual checklist в `docs/runbook/restore.md`: воспроизвести `db_snapshot` → восстановить в `state-restore-test/` → запустить `pytest tests/restore/` (smoke против snapshot'а). Прогон обязателен перед каждым релизом.
4. **Off-site 3-2-1, borg/restic, GFS retention, remote git push** — все deferred (см. [backlog](../backlog.md#q-e-36--backup-wiki-и-state-db)); триггер расширения = переход в `TENANCY_MODE=multi`, размер state >1GB, либо явный запрос юзера на off-site.

**Что MVP-partial backup НЕ покрывает (явно):** аппаратный сбой VPS-диска (полная потеря всех `*-WIKI/` + `state/`), ransomware, физическое уничтожение хоста. Эти классы остаются открытым риском и требуют отдельного решения (off-site target — borg/restic/rclone-to-S3/Backblaze) — за пределами MVP.

**Остальной ops-слой.** Logging — `structlog` JSON-lines в stdout → journald, стабильный набор полей (`ts`, `event`, `correlation_id`, `user_id`, `wiki_id`, `job_id`), correlation context через `contextvars`, PII-redactor processor в pipeline. PII tier-классификация (NIST SP 800-122): Tier-1 DROP (tokens/passwords/keys/cards) → `[REDACTED:tier1:...]`, Tier-2 MASK (email/phone/IBAN) с shape preservation + hash для cross-ref, Tier-3 PLAINTEXT защищается per-store retention + unix-perm 0600. **Retention-таблица Tier-3** (явная разбивка по store'ам, refactor 2026-05-10):

| Store | Retention | Purge mechanism | Rationale |
|-------|-----------|-----------------|-----------|
| `audit.db.chat_log`        | 30d  | APScheduler `chat_log_purge` daily 04:00 UTC | last-20/24h окно классификатора + GDPR-минимум (D-033) |
| `audit.db.tg_updates`      | 24h  | APScheduler `tg_updates_purge` hourly        | dedup TTL, дольше не нужен |
| `audit.db.seen_files`      | 30d  | APScheduler `seen_files_purge` daily         | content-hash dedup window |
| `audit.db.audit_events`    | 90d  | APScheduler `audit_purge` daily              | audit/forensics minimum по NIST SP 800-92 |
| `audit.db.admin_events`    | 90d  | то же                                        | admin-elevate trail |
| `jobs.db.tracker_answers`  | 90d  | APScheduler `tracker_purge` daily            | top-3 prediction window (D-014) |
| `state/snapshots/`         | 7d   | inline в `db_snapshot` job (rolling)         | backup safety net (см. выше) |
| `<wiki>/data/runs/`        | indefinite | no auto-GC in MVP; future retention needs separate decision | full-output replay/audit invariant (D-025) |
| `<wiki>/raw/media/`        | indefinite | manual via NL `purge_wiki` / admin GDPR purge | user-uploaded, immutable per D-022 |
| `_trash/<Domain>-WIKI-<ts>` | 30d | APScheduler `trash_purge` daily              | soft-delete restore window (D-041, см. §5) |

**At-rest crypto** не вводится в MVP (single-tenant); триггер пересмотра — переход в `TENANCY_MODE=multi` с реальными независимыми юзерами или появление Tier-1/Tier-2 PII вне redactor-контура.

**Git per-WIKI:** `git init` на materialize, auto-commit после успешного PostRun-write с `<job_id>(<category>): <title>` форматом, gitleaks pre-commit hook, `.wiki.lock` и `data/runs/` в `.gitignore`.

**Тестирование** трёхуровневое: unit с `FakeClaudeRunner` Protocol (coverage 80% core), integration `RUN_INTEGRATION=1` с реальным CLI subscription nightly, manual E2E checklist перед релизом.

**Log-даты** в runtime WIKI `log.md` — ISO 8601 с TZ-offset до минуты (`## [YYYY-MM-DDTHH:MM±HH:MM] <op> | <title>`), TZ из per-WIKI frontmatter override → user-default → Europe/Moscow; машинные timestamp'ы (audit/chat_log/tracker) — UTC с миллисекундами.

- [D-034](../decisions/D-034-pii-redactor.md) — Tier-1 DROP / Tier-2 MASK / Tier-3 plaintext+retention; write-time hook; GDPR-purge через admin.
- [D-035](../decisions/D-035-service-logging.md) — `structlog` JSON-lines → journald, стабильный набор полей, correlation_id через contextvars.
- [D-036](../decisions/D-036-testing-strategy.md) — unit (FakeClaudeRunner) + integration (real CLI nightly) + manual E2E checklist.
- [D-037](../decisions/D-037-git-in-wiki.md) — git init per-WIKI, auto-commit `<job_id>(<category>):`, gitleaks pre-commit, media в .gitignore.
- [D-038](../decisions/D-038-per-user-systemd.md) — `systemd-run --scope` per CLI-invocation, shared auth dir RO mount, no-Bash tool profile, per-process limits, aggregate slices, `CAP_SETUID + CAP_SETGID`.
- [D-040](../decisions/D-040-log-date-format.md) — runtime `log.md` ISO 8601 + TZ-offset; машинные timestamps UTC мс.
- **Q-E-36 / backup (MVP-partial):** `db_snapshot` internal APScheduler maintenance job (`VACUUM INTO`, 7d local retention) + per-WIKI git history (local, no remote per [D-037](../decisions/D-037-git-in-wiki.md)) + restore-test runbook. Off-site 3-2-1, GFS, remote git push — все deferred ([backlog](../backlog.md#q-e-36--backup-wiki-и-state-db)).

## 11. CLAUDE.md Templates

Доменные пресеты лежат в `ai-steward-wiki/templates/<type>.md` (`health`, `health-lite`, `investment`, `budget`, `family`, `study`, `career`, `home`, `hobby`, `recipes` + `_default.md` fallback) и являются **локальной SSoT** этого репозитория. Никакой live-sync с parent-`CLAUDE.md` сервиса `ai-steward` (TG-бот) не существует — это нарушило бы границу изоляции (Spec-WIKI/CLAUDE.md §1.1). Один раз, при initial bootstrap репо, доменные знания могут быть скопированы из любого внешнего источника (включая parent ai-steward) как стартовая инспирация, после чего шаблоны эволюционируют только PR'ами в этот репозиторий. D-017 amended 2026-05-10: runtime-чтение parent-ai-steward запрещено, а template lookup поддерживает primary slug (`healthlite`) и hyphenated alias (`health-lite`) без расширения D-008 regex для WIKI-директорий. На NL-команду «давай заведём вики для X» (post D-041) router нормализует имя → lookup пресета → копирует с placeholder-substitution → создаёт стандартную структуру (`entities/`, `concepts/`, `raw/`, `index.md`, `log.md`). Inbox `CLAUDE.md` — отдельный shared template в `templates/inbox-wiki/`, содержит router-логику, intent vocabulary и universal pre-flight, без списка доменов (router читает их runtime'ом из `## Inbox hint` каждой Domain-WIKI). Каждый сгенерированный `CLAUDE.md` обязан содержать секцию `## Inbox hint` (1–3 строки, lint-checkable) и YAML frontmatter версионирования (D-039) для evolution через linear migration chain.

- [D-016](../decisions/D-016-inbox-claude-md-template.md) — Inbox-template с intent vocabulary + universal pre-flight; per-domain `## Inbox hint`.
- [D-017](../decisions/D-017-domain-claude-md-template.md) — per-domain пресеты + `_default.md`; локальная SSoT `templates/`, parent ai-steward только one-time bootstrap source.
- [D-039](../decisions/D-039-claude-md-evolution.md) — frontmatter + managed/user-zone секции + linear migration chain v1→v2→…

## 12. Coverage map & open backlog

Overview §9 содержит **39 вопросов** (Tier A 1–8, B 9–19, C 20–24, D 25–30, E 31–39). Из 41 active D-файлов **38 — прямые ответы на Q1–Q39** (1:1), **3 — derived/meta** (`D-001` time-tracker как надстройка над job-model для Q1; `D-014` tracker-memory как sub-design над `D-001`; `D-042` `users.toml` unify как refactor над Q5/Q27/Q28). `D-029` superseded → `D-041` (оба покрывают Q26).

Прямой mapping Q→D (полная таблица):

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
| B | 17 | timeouts/kill           | D-021 | E | 36 | backup              | **MVP-partial / deferred-full** (см. §10, [../backlog.md](../backlog.md#q-e-36--backup-wiki-и-state-db)) |
| B | 18 | cron result routing     | D-020 | E | 37 | git in WIKI         | D-037 |
| B | 19 | cron failure mode       | D-019 | E | 38 | CLAUDE.md evolution | D-039 |
|   |    |                         |       | E | 39 | log date format     | D-040 |

Закрыто полностью: 38/39. Q36 покрыт частично (state-DB snapshots + local per-WIKI git history в MVP; git remote push, 3-2-1 / GFS / borg-restic — deferred).
