# Spec-WIKI — Backlog (deferred decisions)

> Вопросы/решения, явно отложенные до триггера. SSoT для backlog-итемов.
> Формат: `## <Q-ID>` с контекстом, причиной отложки, триггером пересмотра.

**Дата создания:** 2026-05-09

---

## Q-E-36 — Backup WIKI и state-DB

**Статус:** MVP-partial (in-app safety net), полный off-site/GFS — deferred
**Дата отложки:** 2026-05-09
**Дата частичного закрытия:** 2026-05-10 (review tech-spec-draft, critical finding по риску total data-loss в single-tenant SQLite-WAL)

**MVP-объём** (реализационная деталь поверх [D-006](decisions/D-006-state-storage-layout.md) и [D-037](decisions/D-037-git-in-wiki.md), без отдельного D-файла — см. tech-spec §10):
1. APScheduler-job `db_snapshot` daily 03:00 UTC, `VACUUM INTO state/snapshots/<UTC-date>/{jobs,audit,sessions}.db` (consistent SQLite hot-backup без остановки WAL), local retention 7 дней rolling, mode 0700.
2. `git push <remote>` per-WIKI на каждый auto-commit (D-037), config `WIKI_GIT_REMOTE` per-user в `users.toml`, best-effort + retry, audit-event `wiki_push_failed` при отказе, не блокирует UX.
3. Restore-test runbook `docs/runbook/restore.md` (`db_snapshot` → restore → `pytest tests/restore/`), обязателен перед каждым релизом.

**Триггер пересмотра — расширения до полного off-site (любой из):**
1. Реальный инцидент content-loss VPS-уровня (диск, hosting failure).
2. Активация `TENANCY_MODE=multi` через [D-030](decisions/D-030-onboarding.md).
3. Размер state >1GB или WIKI-content >5GB.
4. Явный запрос Henry «настрой off-site backup».

**При расширении — рассмотреть:**
1. WIKI-папки и `state/snapshots/` забираются внешним borg/restic из cron на VPS; service не управляет процессом.
2. Off-site target (S3/B2/Hetzner SB) — выбирает admin.
3. 3-2-1 rule (3 копии / 2 носителя / 1 off-site).
4. Retention: GFS (`--keep-daily 7 --keep-weekly 4 --keep-monthly 6`).

**Связанные:**
1. [D-037](decisions/D-037-git-in-wiki.md) — content-versioning (частично компенсирует).
2. [D-006](decisions/D-006-state-storage-layout.md) — WAL-режим, требует VACUUM INTO для backup.
