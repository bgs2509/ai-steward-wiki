# Q-E-36: Backup WIKI-папок

**Tier:** E
**Источник:** [overview §9 п.36](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

В scope сервиса (rsync/borg) или ответственность админа VPS.

## Варианты

1. **A. Out-of-scope.** Админ настраивает borg/restic/snapshots VPS.
2. **B. In-scope: ежедневный rsync** в S3-compatible.
3. **C. In-scope: git auto-commit** (см. Q-E-37) + push в private remote.

## Решение

- [x] **DEFERRED to backlog** (2026-05-09): backup в MVP не делаем. См. [backlog.md#q-e-36](../backlog.md). Триггер пересмотра: реальный инцидент / multi-tenant / явный запрос Henry. Content-versioning частично закрыт через [D-037](../decisions/D-037-git-in-wiki.md).

## Связанные

1. [Q-E-37: Git внутри WIKI](Q-E-37-git-in-wiki.md)
