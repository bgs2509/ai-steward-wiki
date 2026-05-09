# Q-A-07: Конкурентные запуски Claude

**Tier:** A
**Источник:** [overview §9 п.7](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Один процесс на чат / на WIKI / глобальная очередь / пул с лимитом. Особенно критично при digest-job + concurrent inbox.

## Варианты

1. **A. Один процесс на чат.** Сериализация per-user. Минусы: digest-job блокирует interactive.
2. **B. Один процесс на WIKI.** Естественно сочетается с lock на запись (Q-A-08). Минусы: сложный учёт активных WIKI.
3. **C. Глобальная очередь + пул с лимитом N.** Простой rate-limit. Минусы: head-of-line blocking.
4. **D. Per-user пул + per-WIKI lock.** Гибрид: параллельность между юзерами, сериализация внутри WIKI.

## Решение

- [x] Вариант D — global Semaphore(N) + per-WIKI Lock + PriorityQueue (interactive > tracker > scheduled > digest > ingest). Юзер подтвердил 2026-05-08. См. [D-011](../decisions/D-011-concurrent-claude.md) (accepted).
- [x] оформлено как [D-011](../decisions/D-011-concurrent-claude.md)

## Связанные

1. [Q-A-08: Lock на запись](Q-A-08-lock-on-wiki.md)
