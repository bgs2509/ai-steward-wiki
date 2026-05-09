# Q-B-12: Подтверждение действий в TG

**Tier:** B
**Источник:** [overview §9 п.12](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Inline-кнопки да/нет (структурно) vs free-form ответ vs оба.

## Варианты

1. **A. Только inline-кнопки.** Структурно, парсится надёжно. Минусы: ограничение в выборе вариантов.
2. **B. Только free-form.** Естественно, но требует повторной LLM-классификации.
3. **C. Оба (рекомендуется).** Inline для типовых выборов («да/нет/изменить»), free-form для коррекции («не за 24ч, а за 3»).

## Решение

- [x] **Вариант D** — graduated confirmation (per-category):
  - **Auto-confirm** (zero recap, сразу выполнить + ack): `cancel`, `dismiss`, тривиальные read-actions.
  - **Implicit ack** (recap без обязательного клика, кнопки опциональны): `wiki_query`, `digest`, `today`-list, read-only/idempotent.
  - **Explicit confirm** (обязательный клик / явное «да»): `reminder`/`event`/`medication`-create, wiki write (мутация документов), `delete`.
  - **UX внутри Explicit:** recap-сообщение + 3 inline-кнопки (по `predictive-replies`):
    1. «✅ Подтвердить» (primary).
    2. «✏️ Изменить» / предсказание-альтернатива из `tracker_answers` ([D-014](../decisions/D-014-tracker-memory-model.md)).
    3. «❌ Отмена».
  - **Free-form коррекция:** параллельно принимается всегда; текст вместо клика идёт в router-Claude как correction (FSM держит pending action в `sessions.db.pending_confirmations(pending_id PK, chat_id, draft JSON, created_at, ttl)`).
  - **TTL pending action** 10 мин; по таймауту — silent drop + audit-event.
- [x] оформлено как [D-023](../decisions/D-023-tg-confirmations.md)

## Связанные

1. [Smart inbox routing](../concepts/smart-inbox-routing.md)
