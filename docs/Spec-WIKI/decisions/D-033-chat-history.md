# D-033: Chat history — `chat_log` в audit.db, retention 30d, access классификатору

**Статус:** accepted
**Дата:** 2026-05-09 (amended 2026-05-10 — identity vocabulary aligned with D-042)
**Контекст:** [Q-D-30](../questions/Q-D-30-chat-history.md), overview §7.4 + §2.1 п.3, [D-006](D-006-state-storage-layout.md), [D-009](D-009-classifier-engine.md), [D-014](D-014-tracker-memory-model.md), [Q-E-33](../questions/Q-E-33-audit-pii.md)

## Проблема

Audit-лог по [D-006](D-006-state-storage-layout.md) хранит metadata `(telegram_id, ts, command, cwd, prompt_hash)` без plaintext. Overview §2.1 п.3 упоминает «недавняя история промптов» как сигнал для классификатора Stage-1 ([D-009](D-009-classifier-engine.md)) — без plaintext этот сигнал недоступен. Дополнительно: Henry-admin захочет смотреть «что писал на той неделе» (debug-history). Решить: вводить ли chat_log сейчас или ждать Q-E-33 (audit PII).

## Варианты

1. **A — Только audit-лог (status quo D-006).**
2. **B — `chat_log` в audit.db: full plaintext, retention 30d, access классификатору.** ⭐
3. **C — Per-user opt-in `keep_chat_history: bool` в `users.toml`.**
4. **D — Отложить до Q-E-33, решить связно с PII-policy.**
5. **E — Last-N ring buffer в sessions.db, без отдельной таблицы.**

## Выбор

**Вариант B.**

### Schema

Таблица `chat_log` в `audit.db` ([D-006](D-006-state-storage-layout.md), WAL):

```sql
CREATE TABLE chat_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id INTEGER NOT NULL,           -- canonical external user id, per D-042
  chat_id     INTEGER,                    -- Telegram delivery target, nullable
  ts          TEXT    NOT NULL,           -- ISO 8601 UTC
  direction   TEXT    NOT NULL CHECK (direction IN ('in', 'out')),
  text        TEXT    NOT NULL,           -- plaintext (subject to redaction per Q-E-33)
  session_id  TEXT,                       -- nullable; FK-soft на sessions.db.sessions.id
  prompt_hash TEXT,                       -- SHA-256 для cross-ref с audit metadata
  wiki_id     TEXT                        -- nullable; идентификатор активной WIKI на момент сообщения
);

CREATE INDEX ix_chat_log_telegram_ts ON chat_log(telegram_id, ts DESC);
CREATE INDEX ix_chat_log_session ON chat_log(session_id);
```

### Что пишется

1. **`direction='in'`** — текст команды юзера (после расшифровки voice по [D-022](D-022-voice-photo-input.md), после OCR/vision если был photo).
2. **`direction='out'`** — финальный ответ бота (после streaming-finalization [D-026](D-026-tg-streaming.md), после digest-форматирования [D-024](D-024-digest-format.md)). Промежуточные edit-frames стриминга **не пишутся** — только final.
3. Командные acknowledgements (`/cancel`, `/start`, error reply) — **пишутся** (часть UX-истории).
4. Inline-кнопки/callback_query — пишется text payload (callback_data) с префиксом `[btn]` в `text`.

### Что НЕ пишется

1. Промежуточные стриминг-кадры (см. выше).
2. Файловые тела (PDF/photo binary) — только метаданные в `audit.db.audit_events` per [D-006](D-006-state-storage-layout.md).
3. Embeddings, vector representations — не в скоупе MVP.

### Retention

1. **30 дней** rolling window. Daily cleanup job (APScheduler [D-003](D-003-scheduler-backend.md)): `DELETE FROM chat_log WHERE ts < datetime('now', '-30 days')`.
2. Cleanup-факт логируется в `audit.db.audit_events` (count удалённых строк).
3. Retention настраиваем через env `CHAT_LOG_RETENTION_DAYS=30` (default 30).

### Access pattern

1. **Классификатор Stage-1** ([D-009](D-009-classifier-engine.md)) при роутинге читает last N turns:
   ```sql
   SELECT direction, text, ts FROM chat_log
   WHERE telegram_id = ? AND ts > datetime('now', '-24 hours')
   ORDER BY ts DESC LIMIT 20;
   ```
   N=20, окно 24h — достаточно для §2.1 п.3 «недавняя история». Параметры тюнятся при первом реальном измерении качества.
2. **Admin debug** ([D-028](D-028-admin-access.md)) — read-only через `/admin elevate <USER>` + ad-hoc SQL; не отдельная команда в MVP.
3. **Predictive replies** (concepts/predictive-replies) — продолжает использовать `tracker_answers` ([D-014](D-014-tracker-memory-model.md)), не chat_log. Семантика разная: `tracker_answers` — структурированные ответы на опросные пинги, `chat_log` — свободный диалог.

### PII / redaction

1. **Plaintext пишется как есть** в MVP single-tenant (Henry — собственные данные, liability нулевая).
2. **Redaction policy** (телефоны, emails, токены, API-keys) — определяется в [Q-E-33](../questions/Q-E-33-audit-pii.md) (Волна 8). При принятии Q-E-33 — добавляется write-time redactor; миграция старых строк не требуется (30d retention сам выдавит).
3. До закрытия Q-E-33 — **не логировать API-токены и пароли** даже если юзер их прислал (защита от self-inflicted leak): bot drops чувствительные паттерны до записи в `chat_log` (минимальный hardcoded denylist: `sk-ant-`, `Bearer `, `password=`).

### Multi-tenant readiness

1. Schema готова для multi-tenant ([D-030](D-030-onboarding.md)) — `telegram_id` partition.
2. При добавлении юзера через approve flow — никаких миграций; chat_log начинается с момента первой команды.
3. При removal юзера ([D-031](D-031-allowlist-hot-reload.md) soft-delete) — его строки в chat_log сохраняются до retention expiry; admin может прочитать через elevation. Hard-delete по запросу — отдельная процедура (Q-E-33).

## Последствия

1. Классификатор Stage-1 получает recent-history контекст без последующего refactor schema.
2. Henry-admin имеет debug-видимость диалогов (через elevation flow).
3. PII-нагрузка отложена в Q-E-33; минимальная denylist-защита от self-inflicted leak уже сейчас.
4. Storage cost: ~1MB/день worst case (Henry один, ~100 turns/day × ~10KB) — пренебрежимо для VPS.
5. Запреты:
   1. **Не дублировать `tracker_answers`** в chat_log — разные семантики, разные источники.
   2. **Не писать streaming-кадры** — только final ответ.
   3. **Не пропускать retention cleanup** — без него таблица растёт без ограничений.
   4. **Не отключать denylist** API-tokens/passwords до Q-E-33 redactor.
   5. **Не использовать chat_log как SSoT** для conversation state — это history, не state. State Claude CLI sessions — `sessions.db` ([D-006](D-006-state-storage-layout.md)).

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-033-chat-history.md` (когда финализируется)
