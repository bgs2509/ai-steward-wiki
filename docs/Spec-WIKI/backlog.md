# Spec-WIKI — Backlog (deferred decisions)

> Вопросы/решения, явно отложенные до триггера. SSoT для backlog-итемов.
> Формат: `## <Q-ID>` с контекстом, причиной отложки, триггером пересмотра.

**Дата создания:** 2026-05-09

---

## Q-E-36 — Backup WIKI и state-DB

**Статус:** deferred (no D-file)
**Дата отложки:** 2026-05-09
**Решено в Волне 8:** не делаем backup в MVP.

**Причина:**
1. Single-tenant Henry на одном VPS; admin sets up borg/restic himself when needed.
2. Content-versioning частично закрыт через [D-037](decisions/D-037-git-in-wiki.md) (per-WIKI git, auto-commit) — защищает от bad-edit, но не от disk-failure.
3. SQLite hot-backup procedure (`VACUUM INTO`) не реализуется в-app до триггера.

**Триггер пересмотра (любой из):**
1. Реальный инцидент content-loss (диск, случайный rm).
2. Активация второго tenant через [D-030](decisions/D-030-onboarding.md) approve flow.
3. Явный запрос Henry «настрой backup».

**При пересмотре — рассмотреть:**
1. APScheduler-job `db_snapshot` daily — `VACUUM INTO state/snapshots/<date>/{jobs,audit,sessions}.db` для consistent SQLite-backup; backup-агент забирает snapshots, не live WAL.
2. WIKI-папки бэкапятся внешним borg/restic из cron на VPS; service не управляет процессом.
3. Off-site target (S3/B2/Hetzner SB) — выбирает admin.
4. 3-2-1 rule (3 копии / 2 носителя / 1 off-site).
5. Retention: GFS (`--keep-daily 7 --keep-weekly 4 --keep-monthly 6`).
6. Restore-test procedure обязателен в documentation.

**Связанные:**
1. [D-037](decisions/D-037-git-in-wiki.md) — content-versioning (частично компенсирует).
2. [D-006](decisions/D-006-state-storage-layout.md) — WAL-режим, требует VACUUM INTO для backup.
