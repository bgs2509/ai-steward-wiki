# Q-E-33: Audit-лог PII

**Tier:** E
**Источник:** [overview §9 п.33](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Prompt-hash или plaintext. Срок хранения.

## Варианты

1. **A. Prompt-hash only.** Минимум PII. Для расследования инцидента — недостаточно.
2. **B. Plaintext + retention 30 дней.** Полный контекст. Авто-удаление.
3. **C. Plaintext с шифрованием at-rest.** Ключ — в `.env`.

## Решение

- [x] оформлено как [D-034](../decisions/D-034-pii-redactor.md): tiered write-time redactor (Tier-1 drop / Tier-2 mask / Tier-3 plaintext), без at-rest crypto в MVP, hard-delete через `/admin gdpr_purge`.

## Связанные

1. [Q-D-30: chat history](Q-D-30-chat-history.md)
