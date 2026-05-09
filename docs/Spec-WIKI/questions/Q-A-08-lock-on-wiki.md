# Q-A-08: Lock на запись в WIKI

**Tier:** A
**Источник:** [overview §9 п.8](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Что если cron-digest и `/run` одновременно пишут в одну WIKI? Lock-файл per-wiki / очередь / optimistic.

## Варианты

1. **A. Lock-файл per-wiki (`.wiki.lock`).** Простой, файловый, переживает рестарт.
2. **B. In-memory очередь per-wiki.** Согласовано с Q-A-07 вариант D. Минусы: теряется при рестарте.
3. **C. Optimistic (no lock).** Полагаемся на git/timestamp. Минусы: возможны interleaved writes в `index.md`/`log.md`.

## Решение

- [x] Вариант B — `<wiki>/.wiki.lock` advisory (`fcntl.flock`/`fasteners`) поверх D-011 in-memory lock; stale-recovery по PID; atomic write `tmp+os.replace`. Юзер подтвердил 2026-05-08. См. [D-012](../decisions/D-012-wiki-lock.md) (accepted).
- [x] оформлено как [D-012](../decisions/D-012-wiki-lock.md)

## Связанные

1. [Q-A-07: Конкурентные запуски Claude](Q-A-07-concurrent-claude.md)
