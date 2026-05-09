# Spec-WIKI — Index

> Каталог всех страниц вики. Обновляется при каждом ingest/новой странице.
> Формат: `- [<page>](path) — однострочник`.

**Дата создания:** 2026-05-08
**Источник:** Karpathy LLM Wiki ([gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f))

---

## Schema

- [CLAUDE.md](CLAUDE.md) — конституция вики, конвенции, шаблоны
- [log.md](log.md) — append-only хронология действий LLM
- [queue.md](queue.md) — очередь разбора оставшихся вопросов (по архитектурной важности)
- [backlog.md](backlog.md) — отложенные решения с триггерами пересмотра

## Entities

- [inbox-wiki](entities/inbox-wiki.md) — per-user точка входа TG, триаж/router
- [classifier](entities/classifier.md) — Stage-1 Claude/Haiku: выбор WIKI и сессии
- [router-agent](entities/router-agent.md) — Claude в Inbox-WIKI: классификация + действия
- [job-model](entities/job-model.md) — унифицированный объект расписания (6+ kinds, Flat + JSON payload)
- [domain-wiki](entities/domain-wiki.md) — sibling `<Domain>-WIKI/`, librarian-режим
- [time-tracker](entities/time-tracker.md) — фасад фичи трекинга времени (опросы + обязательные дела)

## Concepts

- [two-stage-launch](concepts/two-stage-launch.md) — Классификатор → Исполнитель
- [anti-nesting](concepts/anti-nesting.md) — запрет WIKI внутри WIKI
- [sibling-only-domains](concepts/sibling-only-domains.md) — горизонтальный рост доменов
- [smart-inbox-routing](concepts/smart-inbox-routing.md) — безкомандный UX через Inbox + Router
- [llm-wiki-method](concepts/llm-wiki-method.md) — Karpathy: 3 слоя, 3 операции
- [predictive-replies](concepts/predictive-replies.md) — 3 инлайн-кнопки с предсказаниями + «другое»
- [schedule-profiles](concepts/schedule-profiles.md) — будни/выходные/праздники, окно 06:00–23:00
- [mandatory-checkins](concepts/mandatory-checkins.md) — обязательные дела + post-action follow-up

## Decisions

- [D-001](decisions/D-001-time-tracker-vs-job-model.md) — time-tracker как надстройка над job-model (proposed)
- [D-002](decisions/D-002-job-model-storage.md) — job-model storage: Flat + JSON payload (accepted)
- [D-003](decisions/D-003-scheduler-backend.md) — scheduler backend: APScheduler AsyncIOScheduler + SQLAlchemyJobStore (accepted)
- [D-004](decisions/D-004-inbox-wiki-scope.md) — Inbox-WIKI per-user + shared template (accepted)
- [D-005](decisions/D-005-no-planner-json.md) — никакого planner.json: только jobs.db (accepted)
- [D-006](decisions/D-006-state-storage-layout.md) — state storage: 3 БД (jobs/audit/sessions), WAL (accepted)
- [D-007](decisions/D-007-add-dir-scope.md) — `--add-dir` only `<wiki>`, профиль через CLAUDE.md auto-walk (accepted)
- [D-008](decisions/D-008-wiki-marker-format.md) — WIKI marker regex `^[A-Z][A-Za-z0-9]*-WIKI$` (accepted)
- [D-009](decisions/D-009-classifier-engine.md) — classifier engine: Haiku Stage-0 → CLI Sonnet Stage-1 (accepted)
- [D-010](decisions/D-010-nl-time-parsing.md) — NL time parsing: dateparser → Haiku fallback (accepted)
- [D-011](decisions/D-011-concurrent-claude.md) — concurrent Claude CLI: Semaphore + per-WIKI Lock + PriorityQueue (accepted)
- [D-012](decisions/D-012-wiki-lock.md) — WIKI on-disk lock: `.wiki.lock` advisory + atomic write (accepted)
- [D-013](decisions/D-013-claude-cli-auth.md) — Claude CLI auth: subscription mode, single-tenant Henry-N (accepted)
- [D-014](decisions/D-014-tracker-memory-model.md) — tracker memory: append-only `tracker_answers` в jobs.db, 90d retention (accepted)
- [D-015](decisions/D-015-system-prompt-inject.md) — system prompt: hybrid `--append-system-prompt @prompts/wiki.md` + per-WIKI CLAUDE.md (accepted)
- [D-016](decisions/D-016-inbox-claude-md-template.md) — Inbox-WIKI CLAUDE.md: hybrid autodiscover + per-domain `## Inbox hint` (accepted)
- [D-017](decisions/D-017-domain-claude-md-template.md) — domain-WIKI CLAUDE.md: per-domain пресеты + `_default` fallback (accepted)
- [D-018](decisions/D-018-ingest-idempotency.md) — ingest dedup: L1 TG `update_id` (24h) + L2 SHA-256 `seen_files` (30d) (accepted)
- [D-019](decisions/D-019-cron-failure-mode.md) — cron failure: error taxonomy + retry exp.backoff + DLQ + auto-disable (accepted)
- [D-020](decisions/D-020-cron-result-routing.md) — cron result routing: per-category `notify_policy` + admin shadow channel (accepted)
- [D-021](decisions/D-021-timeouts-kill-policy.md) — Claude CLI timeouts: per-category + `/cancel` + orphan cleanup (accepted)
- [D-022](decisions/D-022-voice-photo-input.md) — voice/photo: faster-whisper + Claude vision (no pytesseract) (accepted)
- [D-023](decisions/D-023-tg-confirmations.md) — TG confirmations: graduated (auto / implicit / explicit) (accepted)
- [D-024](decisions/D-024-digest-format.md) — digest: HTML summary + actionable cards для critical (accepted)
- [D-025](decisions/D-025-output-size.md) — output size: ≤3500 inline / ≤10000 split / >10000 summary+document (accepted)
- [D-026](decisions/D-026-tg-streaming.md) — streaming: edit-mode + chain split при 4000 + 1.5s throttle (accepted)
- [D-027](decisions/D-027-anti-nesting-admin-boundary.md) — anti-nesting boundary: `WORKSPACE_ROOT` единый anchor (accepted)
- [D-028](decisions/D-028-admin-access.md) — admin access: single-tenant full-run + multi-tenant break-glass elevation (accepted)
- [D-029](decisions/D-029-wiki-init-auth.md) — `/wiki_init`: user creates + auto-suggest + soft limit 20 + reversible delete (superseded-by D-041)
- [D-030](decisions/D-030-onboarding.md) — onboarding: hybrid `users.toml` SSoT + `/start`-flow за `ENABLE_SELF_SIGNUP` флагом (accepted)
- [D-031](decisions/D-031-allowlist-hot-reload.md) — allowlist hot-reload: SIGHUP + watchdog + validate-before-swap (accepted)
- [D-032](decisions/D-032-multi-language.md) — multi-language: MVP-ru-only, no i18n (accepted)
- [D-033](decisions/D-033-chat-history.md) — chat history: `chat_log` в audit.db, retention 30d, classifier last-20/24h (accepted)
- [D-034](decisions/D-034-pii-redactor.md) — PII redactor: tiered write-time (drop/mask/plaintext), без at-rest crypto в MVP (accepted)
- [D-035](decisions/D-035-service-logging.md) — service logging: structlog → stdout JSON → journald + PII processor (accepted)
- [D-036](decisions/D-036-testing-strategy.md) — testing: pyramid unit + integration (RUN_INTEGRATION=1) + manual e2e (accepted)
- [D-037](decisions/D-037-git-in-wiki.md) — git per-WIKI: auto-commit + gitleaks pre-commit + no remote MVP (accepted)
- [D-038](decisions/D-038-per-user-systemd.md) — hard isolation MVP: `systemd-run --scope --uid` per Claude CLI (accepted)
- [D-039](decisions/D-039-claude-md-evolution.md) — CLAUDE.md schema evolution: versioning + managed sections + 3-way merge + TG confirm (accepted)
- [D-040](decisions/D-040-log-date-format.md) — log.md date format: ISO 8601 с TZ-offset, minute-granularity, per-WIKI override (accepted)
- [D-041](decisions/D-041-no-direct-wiki-commands.md) — WIKI lifecycle только через NL-промпт; убраны `/wiki_init`/`/wiki_delete`/`/wiki_restore`/`/wiki_purge`; обязательный duplicate-check (accepted, supersedes D-029)

## Questions

- [Q-A-01](questions/Q-A-01-job-table.md) — Job: одна таблица vs три
- [Q-A-09](questions/Q-A-09-tracker-memory-model.md) — где хранить паттерны юзера для predictive-replies
- [Q-A-02](questions/Q-A-02-scheduler-backend.md) — APScheduler vs crontab vs гибрид
- [Q-A-03](questions/Q-A-03-inbox-scope.md) — Inbox per-user vs global
- [Q-A-04](questions/Q-A-04-classifier-engine.md) — Claude CLI vs Haiku vs гибрид
- [Q-A-05](questions/Q-A-05-nl-time-parsing.md) — NL-парсинг времени
- [Q-A-06](questions/Q-A-06-planner-ssot.md) — planner.json: cross-service vs миграция
- [Q-A-07](questions/Q-A-07-concurrent-claude.md) — Конкурентные запуски Claude
- [Q-A-08](questions/Q-A-08-lock-on-wiki.md) — Lock на запись в WIKI
- [Q-B-09](questions/Q-B-09-inbox-claude-md-template.md) — шаблон Inbox-WIKI CLAUDE.md
- [Q-B-10](questions/Q-B-10-domain-claude-md-template.md) — шаблон domain-WIKI CLAUDE.md
- [Q-B-11](questions/Q-B-11-ingest-idempotency.md) — идемпотентность ingest
- [Q-B-12](questions/Q-B-12-tg-confirmations.md) — подтверждение действий в TG
- [Q-B-13](questions/Q-B-13-digest-format.md) — digest-формат ответа
- [Q-B-14](questions/Q-B-14-voice-photo-input.md) — голос/фото-вход
- [Q-B-15](questions/Q-B-15-tg-streaming.md) — стриминг Claude → TG
- [Q-B-16](questions/Q-B-16-output-size.md) — размер вывода Claude
- [Q-B-17](questions/Q-B-17-timeouts-kill.md) — таймауты и kill-policy
- [Q-B-18](questions/Q-B-18-cron-result-routing.md) — cron-результат без активного чата
- [Q-B-19](questions/Q-B-19-cron-failure.md) — failure mode для cron
- [Q-C-20](questions/Q-C-20-claude-cli-auth.md) — аутентификация Claude CLI
- [Q-C-21](questions/Q-C-21-system-prompt-inject.md) — инжект Wiki system prompt
- [Q-C-22](questions/Q-C-22-add-dir-scope.md) — `--add-dir` область
- [Q-C-23](questions/Q-C-23-wiki-marker-format.md) — формат WIKI-маркера
- [Q-C-24](questions/Q-C-24-anti-nesting-admin.md) — anti-nesting граница для admin
- [Q-D-25](questions/Q-D-25-admin-access.md) — доступ admin к чужим USERS/
- [Q-D-26](questions/Q-D-26-wiki-init-auth.md) — `/wiki_init` авторизация
- [Q-D-27](questions/Q-D-27-onboarding.md) — onboarding нового юзера
- [Q-D-28](questions/Q-D-28-allowlist-hot-reload.md) — allowlist hot-reload
- [Q-D-29](questions/Q-D-29-multi-language.md) — multi-language
- [Q-D-30](questions/Q-D-30-chat-history.md) — история чата
- [Q-E-31](questions/Q-E-31-per-user-systemd.md) — per-user systemd unit
- [Q-A-32](questions/Q-A-32-state-storage.md) — хранилище состояния (повышен из E)
- [Q-E-33](questions/Q-E-33-audit-pii.md) — audit-лог PII
- [Q-E-34](questions/Q-E-34-service-logging.md) — логирование сервиса
- [Q-E-35](questions/Q-E-35-testing.md) — тестирование
- [Q-E-36](questions/Q-E-36-backup.md) — backup WIKI-папок
- [Q-E-37](questions/Q-E-37-git-in-wiki.md) — git внутри WIKI
- [Q-E-38](questions/Q-E-38-claude-md-evolution.md) — schema эволюция CLAUDE.md
- [Q-E-39](questions/Q-E-39-log-date-format.md) — format даты в log.md

## Research

- [overview-2026-05-07](research/overview-2026-05-07.md) — саммари главного описания сервиса

## Raw

- [raw/20260507-ai-steward-wiki-only-overview.md](raw/20260507-ai-steward-wiki-only-overview.md) — основное описание сервиса (§1–§11)

---

## Внешние ссылки

1. [Karpathy LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
2. [Karpathy tweet](https://x.com/karpathy/status/2039805659525644595)
3. [Obsidian](https://obsidian.md/download)
