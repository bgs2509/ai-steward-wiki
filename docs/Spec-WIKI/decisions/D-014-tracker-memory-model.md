# D-014: tracker memory — append-only `tracker_answers` в jobs.db

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-09](../questions/Q-A-09-tracker-memory-model.md), [predictive-replies](../concepts/predictive-replies.md), [D-005](D-005-no-planner-json.md), [D-006](D-006-state-storage-layout.md)

## Проблема

Predictive-replies (3 inline-кнопки + «другое») требуют ответа: «что Henry-N обычно отвечал в (день_недели, час)». Нужно хранилище истории ответов трекера + способ агрегации топ-3, согласованный с D-005 (jobs.db — SSoT) и D-006 (3 БД: jobs/audit/sessions; tracker memory зарезервирован в jobs.db).

## Варианты

1. **A — append-only `tracker_answers` table в jobs.db** ⭐ Best Practice (event sourcing + projection): индексированный SELECT для топ-3 на лету.
2. **B — JSONL `data/tracker_log.jsonl`:** human-readable, но full-scan, lock на запись, нарушает D-005/D-006.
3. **C — pre-aggregated `patterns.json`:** zero query cost, но stale, теряет сырой лог, ломается при ручной правке.
4. **D — отдельная `tracker.db`:** изоляция, но кросс-БД join'ы с `jobs.id`, нарушает D-006 «3 БД».

## Выбор

**Вариант A.** Append-only таблица в jobs.db (D-006 уже зарезервировал место).

### Схема

```sql
CREATE TABLE tracker_answers (
    id INTEGER PRIMARY KEY,
    owner_telegram_id INTEGER NOT NULL,
    slot_dow INTEGER NOT NULL,           -- 0..6 (Mon=0)
    slot_hour INTEGER NOT NULL,          -- 0..23
    answer TEXT NOT NULL,
    answered_at TIMESTAMP NOT NULL,      -- UTC
    source TEXT NOT NULL,                -- 'predicted' | 'other' | 'tracker_followup'
    job_id TEXT REFERENCES jobs(id)
);
CREATE INDEX idx_tracker_slot
  ON tracker_answers(owner_telegram_id, slot_dow, slot_hour, answered_at);
```

### Top-3 query (on-the-fly)

```sql
SELECT answer, COUNT(*) AS freq
FROM tracker_answers
WHERE owner_telegram_id=? AND slot_dow=? AND slot_hour=?
  AND answered_at > datetime('now', '-90 days')
GROUP BY answer ORDER BY freq DESC LIMIT 3;
```

### Параметры (v1)

1. **Retention:** 90 дней rolling; ежедневный maintenance-job делает `DELETE WHERE answered_at < now-90d`.
2. **Recency-bias:** в v1 — нет (простой COUNT). В v2 — опциональный `weight = exp(-Δt/30d)`.
3. **Slot granularity:** `(dow, hour)` — 168 слотов/неделю.
4. **Source-tag** различает «нажал predicted-кнопку» / «ввёл свой ответ» / «follow-up после mandatory».

## Последствия

1. Запись в `tracker_answers` идёт в той же транзакции, что fire-event у `tracker_followup`-job — атомарность.
2. Индексированный запрос <5ms на десятках тысяч строк — приемлемо для inline-keyboard рендеринга.
3. Эволюционируемость: recency-bias, exclusions, per-domain слоты — добавляются SQL'ем без миграции данных.
4. SoC соблюдён: jobs.db = scheduling+tracking, audit.db = compliance, sessions.db = TG runtime.
5. FK `job_id → jobs(id)` даёт связность между ответом и инициировавшим вопросом (для debug/audit).
6. Maintenance-job (pruning) — отдельный `system_maintenance`-kind job в jobs.db, daily 04:00 local.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-014-tracker-memory.md` (когда финализируется)
