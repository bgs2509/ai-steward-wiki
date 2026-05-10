# Spec-WIKI — Decision Queue

> Очередь разбора оставшихся вопросов в порядке архитектурной важности (topological sort по зависимостям). Обновляется по мере закрытия вопросов.
> SSoT для **приоритета**. SSoT для **каталога** — [index.md](index.md).
> Формат: `- [ ] [Q-ID](path) — однострочник` (галочка ставится при принятии решения).

**Дата создания:** 2026-05-08
**Источник:** диалог 2026-05-08 (relevance assessment после D-001/D-002/D-003)

---

## Принятые (для контекста)

- [x] [Q-A-01](questions/Q-A-01-job-table.md) → [D-002](decisions/D-002-job-model-storage.md) — Flat + JSON payload
- [x] [Q-A-02](questions/Q-A-02-scheduler-backend.md) → [D-003](decisions/D-003-scheduler-backend.md) — APScheduler AsyncIOScheduler
- [x] [Q-A-03](questions/Q-A-03-inbox-scope.md) → [D-004](decisions/D-004-inbox-wiki-scope.md) — Inbox per-user + shared template
- [x] [Q-A-06](questions/Q-A-06-planner-ssot.md) → [D-005](decisions/D-005-no-planner-json.md) — никакого planner.json: только jobs.db
- [x] [Q-A-32](questions/Q-A-32-state-storage.md) → [D-006](decisions/D-006-state-storage-layout.md) — 3 БД (jobs/audit/sessions), WAL
- [x] [Q-C-22](questions/Q-C-22-add-dir-scope.md) → [D-007](decisions/D-007-add-dir-scope.md) — `--add-dir` only `<wiki>`, профиль через auto-walk
- [x] [Q-C-23](questions/Q-C-23-wiki-marker-format.md) → [D-008](decisions/D-008-wiki-marker-format.md) — regex `^[A-Z][A-Za-z0-9]*-WIKI$`
- [x] [Q-A-04](questions/Q-A-04-classifier-engine.md) → [D-009](decisions/D-009-classifier-engine.md) — гибрид Haiku Stage-0 → CLI Sonnet Stage-1
- [x] [Q-A-05](questions/Q-A-05-nl-time-parsing.md) → [D-010](decisions/D-010-nl-time-parsing.md) — dateparser + Haiku fallback
- [x] [Q-A-07](questions/Q-A-07-concurrent-claude.md) → [D-011](decisions/D-011-concurrent-claude.md) — Semaphore + per-WIKI Lock + PriorityQueue
- [x] [Q-A-08](questions/Q-A-08-lock-on-wiki.md) → [D-012](decisions/D-012-wiki-lock.md) — `.wiki.lock` advisory + atomic write
- [x] [Q-C-20](questions/Q-C-20-claude-cli-auth.md) → [D-013](decisions/D-013-claude-cli-auth.md) — subscription mode, single-tenant Henry-N
- [x] [Q-A-09](questions/Q-A-09-tracker-memory-model.md) → [D-014](decisions/D-014-tracker-memory-model.md) — append-only `tracker_answers` в jobs.db
- [x] [Q-C-21](questions/Q-C-21-system-prompt-inject.md) → [D-015](decisions/D-015-system-prompt-inject.md) — hybrid system prompt inject
- [x] [Q-B-09](questions/Q-B-09-inbox-claude-md-template.md) → [D-016](decisions/D-016-inbox-claude-md-template.md) — Inbox-WIKI hybrid autodiscover + `## Inbox hint`
- [x] [Q-B-10](questions/Q-B-10-domain-claude-md-template.md) → [D-017](decisions/D-017-domain-claude-md-template.md) — per-domain пресеты + `_default`
- [x] [Q-B-11](questions/Q-B-11-ingest-idempotency.md) → [D-018](decisions/D-018-ingest-idempotency.md) — L1 TG update_id + L2 content hash
- [x] [Q-B-19](questions/Q-B-19-cron-failure.md) → [D-019](decisions/D-019-cron-failure-mode.md) — error taxonomy + retry + DLQ + auto-disable
- [x] [Q-B-18](questions/Q-B-18-cron-result-routing.md) → [D-020](decisions/D-020-cron-result-routing.md) — per-category notify_policy + admin shadow
- [x] [Q-B-17](questions/Q-B-17-timeouts-kill.md) → [D-021](decisions/D-021-timeouts-kill-policy.md) — per-category timeouts + `/cancel`
- [x] [Q-B-14](questions/Q-B-14-voice-photo-input.md) → [D-022](decisions/D-022-voice-photo-input.md) — faster-whisper + Claude vision
- [x] [Q-B-12](questions/Q-B-12-tg-confirmations.md) → [D-023](decisions/D-023-tg-confirmations.md) — graduated confirmation
- [x] [Q-B-13](questions/Q-B-13-digest-format.md) → [D-024](decisions/D-024-digest-format.md) — HTML summary + critical cards
- [x] [Q-B-16](questions/Q-B-16-output-size.md) → [D-025](decisions/D-025-output-size.md) — threshold-based hybrid
- [x] [Q-B-15](questions/Q-B-15-tg-streaming.md) → [D-026](decisions/D-026-tg-streaming.md) — edit + chain split
- [x] [Q-C-24](questions/Q-C-24-anti-nesting-admin.md) → [D-027](decisions/D-027-anti-nesting-admin-boundary.md) — `WORKSPACE_ROOT` единый anchor
- [x] [Q-D-25](questions/Q-D-25-admin-access.md) → [D-028](decisions/D-028-admin-access.md) — single-tenant full-run + multi-tenant elevation
- [x] [Q-D-26](questions/Q-D-26-wiki-init-auth.md) → [D-029](decisions/D-029-wiki-init-auth.md) — user creates + auto-suggest + soft limit + reversible delete
- [x] [Q-D-27](questions/Q-D-27-onboarding.md) → [D-030](decisions/D-030-onboarding.md) — hybrid `users.toml` + `/start`-flow за флагом
- [x] [Q-D-28](questions/Q-D-28-allowlist-hot-reload.md) → [D-031](decisions/D-031-allowlist-hot-reload.md) — SIGHUP + watchdog + validate
- [x] [Q-D-29](questions/Q-D-29-multi-language.md) → [D-032](decisions/D-032-multi-language.md) — MVP-ru-only, no i18n
- [x] [Q-D-30](questions/Q-D-30-chat-history.md) → [D-033](decisions/D-033-chat-history.md) — `chat_log` в audit.db, retention 30d
- [x] [Q-E-33](questions/Q-E-33-audit-pii.md) → [D-034](decisions/D-034-pii-redactor.md) — tiered write-time PII redactor, без at-rest crypto
- [x] [Q-E-34](questions/Q-E-34-service-logging.md) → [D-035](decisions/D-035-service-logging.md) — structlog → stdout JSON → journald
- [x] [Q-E-35](questions/Q-E-35-testing.md) → [D-036](decisions/D-036-testing-strategy.md) — test pyramid + RUN_INTEGRATION=1 + manual e2e
- [x] [Q-E-36](questions/Q-E-36-backup.md) → DEFERRED ([backlog.md](backlog.md)) — backup в MVP не делаем
- [x] [Q-E-37](questions/Q-E-37-git-in-wiki.md) → [D-037](decisions/D-037-git-in-wiki.md) — git per-WIKI + auto-commit + gitleaks
- [x] [Q-E-31](questions/Q-E-31-per-user-systemd.md) → [D-038](decisions/D-038-per-user-systemd.md) — hard isolation MVP via `systemd-run`
- [x] [Q-E-38](questions/Q-E-38-claude-md-evolution.md) → [D-039](decisions/D-039-claude-md-evolution.md) — schema versioning + managed sections + 3-way merge
- [x] [Q-E-39](questions/Q-E-39-log-date-format.md) → [D-040](decisions/D-040-log-date-format.md) — ISO 8601 с TZ-offset, minute-granularity

---

## Волна 1 — Foundation / SSoT — ✅ закрыта

## Волна 2 — Engine / runtime — ✅ закрыта

## Волна 3 — Tracker / payloads — ✅ закрыта

## Волна 4 — Контракты содержимого — ✅ закрыта

## Волна 5 — UX runtime — ✅ закрыта

## Волна 6 — Admin / access — ✅ закрыта

## Волна 7 — Polish UX — ✅ закрыта

## Волна 8 — Operations — ✅ закрыта

**Итог по всем волнам:** 41 решение accepted (D-001…D-042, из них D-029 superseded-by D-041) + 1 отложено в backlog (Q-E-36). Все 39 вопросов из overview §9 закрыты; Q-A-09 — дополнительный вопрос из time-tracker ingest.
