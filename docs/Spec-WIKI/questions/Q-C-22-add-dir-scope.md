# Q-C-22: `--add-dir` область

**Tier:** C
**Источник:** [overview §9 п.22](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Только сама WIKI или включая `USERS/<NAME>/CLAUDE.md` (для чтения профиля юзера).

## Варианты

1. **A. Только `<wiki>`.** Максимальная изоляция. Профиль не виден — ответы менее персонализированные.
2. **B. `<wiki>` + readonly `USERS/<NAME>/CLAUDE.md`.** Профиль доступен; запись запрещена `--permission-mode acceptEdits` + path-фильтр.
3. **C. `<wiki>` + read весь `USERS/<NAME>/`.** Cross-WIKI чтение возможно (опасно для приватности между доменами).

## Решение

- [x] Вариант A (`--add-dir <wiki>` only; профиль через CLAUDE.md auto-walk; digest-job — поимённый список WIKI). Юзер подтвердил 2026-05-08. См. [D-007](../decisions/D-007-add-dir-scope.md) (accepted).

## Связанные

1. [Domain-WIKI](../entities/domain-wiki.md)
