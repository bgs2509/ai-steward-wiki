# D-023: TG confirmations — graduated confirmation (per-category)

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-B-12](../questions/Q-B-12-tg-confirmations.md), overview §9.12, §8.3, [D-014](D-014-tracker-memory-model.md), [D-018](D-018-ingest-idempotency.md), [predictive-replies](../concepts/predictive-replies.md)

## Проблема

Подтверждение действий перед записью в `jobs.db` / wiki. Слишком навязчиво — friction; слишком слабо — silent ошибки в medication / wiki writes.

## Варианты

1. **A — Только inline-кнопки.**
2. **B — Только free-form.**
3. **C — Hybrid (inline + free-form всегда).**
4. **D — Graduated confirmation per-category** (auto / implicit / explicit). ⭐
5. **E — Auto-recap без подтверждения** + `/undo`.

## Выбор

**Вариант D (graduated confirmation).**

### Confirmation levels

| Level | Категории | UX |
|-------|-----------|-----|
| **auto-confirm** | `cancel`, `dismiss`, тривиальные read-only | выполнить сразу, ack 1 строкой |
| **implicit ack** | `wiki_query`, `digest`, `today`-list (read-only/idempotent) | recap + опциональные кнопки, не блокирует на клик |
| **explicit confirm** | `reminder`/`event`/`medication`-create, wiki-write, `delete` | recap + 3 inline-кнопки + free-form fallback, **обязателен** клик/ответ |

### Explicit confirm UX

1. Bot шлёт recap-сообщение в HTML format (по [D-024](D-024-digest-format.md)): что бот понял из запроса.
2. Inline-keyboard, 3 кнопки (по [predictive-replies](../concepts/predictive-replies.md)):
   1. **«✅ Подтвердить»** (primary action) — записать в jobs.db.
   2. **«✏️ Изменить»** или предсказание-альтернатива (топ-1 паттерн из `tracker_answers` по [D-014](D-014-tracker-memory-model.md)) — открывает корректирующий prompt.
   3. **«❌ Отмена»** — drop draft, audit-event.
3. Параллельно **free-form input** принимается всегда:
   1. Юзер пишет текст вместо клика → router-Claude получает текст как correction вместе с draft.
   2. На correction → apply diff к draft → новый recap + те же 3 кнопки.

### Storage — pending confirmations

```
sessions.db.pending_confirmations(
  pending_id TEXT PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  draft TEXT NOT NULL,             -- JSON job-payload до commit
  category TEXT NOT NULL,
  recap_message_id INTEGER,
  inline_keyboard_id INTEGER,
  created_at INTEGER NOT NULL,
  ttl_sec INTEGER NOT NULL DEFAULT 600,
  status TEXT DEFAULT 'pending'    -- pending | confirmed | corrected | cancelled | expired
)
```

INDEX по `(chat_id, status, created_at DESC)` для fast lookup на следующее сообщение от юзера.

### TTL

1. **Default 10 минут.** По истечении — `status='expired'`, recap-сообщение редактируется в «⏱ confirmation expired», audit-event.
2. **Override через payload** для специфичных категорий (например, medication-suggestion — 60 мин: дать время решить).

### Race conditions

1. Один pending confirmation per `(chat_id, category)` — новый запрос той же категории cancel'ит предыдущий silent (audit-event).
2. Confirm-vs-expire — транзакционный transition `pending → confirmed` под `WHERE status='pending'`; expire wins на TTL passed.

## Последствия

1. UX-friction оптимизирован по категории stake'а: zero-friction для read, safety для writes.
2. Free-form коррекции работают inline без отдельной команды.
3. Predictive-replies встроены в кнопку 2 — органично для UX.
4. Запреты:
   1. **Не делать `auto-confirm` для destructive операций** (даже если payload явно указан).
   2. **Не игнорировать free-form input** при наличии pending confirmation.
   3. **Не оставлять pending без TTL** — leak в `sessions.db`.
5. Будущее: расширить decision-table per-domain (Health-WIKI: `medication-create` всегда explicit с вторым подтверждением для опасных доз).

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-023-tg-confirmations.md` (когда финализируется)
