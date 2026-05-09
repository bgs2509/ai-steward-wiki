# Q-E-37: Git внутри WIKI-папок

**Tier:** E
**Источник:** [overview §9 п.37](../raw/20260507-ai-steward-wiki-only-overview.md), §7a

## Формулировка

§7a «git опционально» — авто-commit после каждого ingest/query или нет.

## Варианты

1. **A. Без git.** Простота. Backup отдельно.
2. **B. Git per-WIKI, auto-commit после каждого PostRun.** История, undo.
3. **C. Git per-USER (один репо на `USERS/<NAME>/`).** Меньше overhead.

## Решение

- [x] оформлено как [D-037](../decisions/D-037-git-in-wiki.md): git per-WIKI (не per-USER), auto-commit после PostRun, gitleaks pre-commit hook, gitignore voice/photo, no remote в MVP.

## Связанные

1. [Q-E-36: Backup](Q-E-36-backup.md)
