# Q-D-30: История чата

**Tier:** D
**Источник:** [overview §9 п.30](../raw/20260507-ai-steward-wiki-only-overview.md), §7.4

> **Update ([D-042](../decisions/D-042-unify-user-config.md), 2026-05-10):** в Варианте C `roles.toml` → `users.toml`. На принятый Вариант B не влияет.

## Формулировка

Журнал команд/ответов в SQLite сверх audit-лога §7.4.

## Варианты

1. **A. Только audit-лог.** `(user_id, ts, command, cwd, prompt-hash)`. Без plaintext.
2. **B. Audit + chat_log таблица.** Полные сообщения для retrieval классификатором (§2.1 п.3 «недавняя история промптов»).
3. **C. Опционально per-user.** `roles.toml` поле `keep_chat_history: bool`.

## Решение

- [x] оформлено как [D-033](../decisions/D-033-chat-history.md) — Вариант B (`chat_log` в audit.db, retention 30d, last-20/24h окно для классификатора Stage-1, минимальный denylist для tokens/passwords; полная redaction-policy → Q-E-33).

## Связанные

1. [Q-E-33: Audit PII](Q-E-33-audit-pii.md), [Classifier](../entities/classifier.md)
