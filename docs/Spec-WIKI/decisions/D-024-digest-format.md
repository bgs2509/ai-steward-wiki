# D-024: digest format — HTML summary + actionable cards только для critical

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-B-13](../questions/Q-B-13-digest-format.md), overview §9.13, [D-020](D-020-cron-result-routing.md), [D-021](D-021-timeouts-kill-policy.md), [D-023](D-023-tg-confirmations.md)

## Проблема

`digest_job` (утренний / вечерний / weekly / `/today`) генерирует список jobs/событий/tracker-сводок. Нужен presentation-формат: plain / markdown / cards-with-actions, с учётом TG лимита 4096 chars/msg.

## Варианты

1. **A — Plain text.**
2. **B — HTML, read-only.**
3. **C — HTML + per-item кнопки на всём.**
4. **D — Hybrid: summary HTML + actionable cards только для critical.** ⭐
5. **E — Pure cards (Linear-style).**

## Выбор

**Вариант D (hybrid).**

### Parse mode

**HTML** (не MarkdownV2). Поддерживается: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a href="…">`, `<blockquote>`. Escape: `<`, `>`, `&` → entities (стандартная функция в aiogram).

**Reason:** MarkdownV2 escape — минное поле (`_*[]()~>#+-=|{}.!`), один `.` → API error. HTML — индустриально стабильнее, рекомендован aiogram.

### Структура summary

```
<b>📅 Сегодня</b>
1. 09:00 — приём ферретаб 💊
2. 11:00 — встреча с Иваном 🤝
3. 18:30 — забрать дочь из садика 👧

<b>💊 Лекарства</b>
1. 09:00 ферретаб (последний за неделю)

<b>📈 Tracker</b>
- Сон: 7.2ч (норма)
- Шаги вчера: 4 521 (-2 100 vs avg)

<b>📝 Wiki updates</b>
- Health: добавлен анализ крови от 2026-05-08
```

### TL;DR

**Первая секция** digest'а — компактный TL;DR (3–5 строк) для скан-чтения с мобильного. Генерируется Stage-1 как часть digest-prompt'а.

### Actionable cards

Отдельные сообщения с inline-кнопками шлются **только** для items, требующих действия в окне **±2h**:

| Тип | Кнопки | Источник |
|-----|--------|----------|
| `medication`-due-now | «✅ Принял» / «⏰ +30мин» / «❌ Skip» | jobs.db |
| `event`-soon | «📍 Я в пути» / «⏰ Опаздываю» / «❌ Отменить» | jobs.db |
| `pending_confirmation` | «✅ Подтвердить» / «✏️ Изменить» / «❌ Отмена» | sessions.db ([D-023](D-023-tg-confirmations.md)) |

Read-only items (на завтра, статистика, wiki updates) — **в summary без кнопок**.

### Длинный digest (>4096)

1. Split по секциям — границы выбираются по `<b>` headers.
2. Continuity-маркер в footer каждого msg: `(1/3)`, `(2/3)`, `(3/3)`.
3. Если итоговый размер всё равно превышает Q-B-16 threshold — fallback на [D-025](D-025-output-size.md) (`send_document`).

### `/expand <section>`

Команда для on-demand детализации секции. Например, `/expand tracker` — полная сводка трекера за период (vs одна строка в summary).

### Согласование с D-020

`digest_job.notify_policy = always` (D-020), категория `digest` — поэтому summary owner получает всегда, даже если пустой («сегодня дел нет 🌿»).

## Последствия

1. Mobile-first, scan-friendly summary.
2. Critical actions actionable inline; non-critical — без noise.
3. HTML escape стабильнее MarkdownV2.
4. Запреты:
   1. **Не использовать MarkdownV2.**
   2. **Не делать кнопки на read-only items** (chat noise).
   3. **Не разрывать `<...>` теги** на split'е по 4096 (D-026 обработка).
5. Будущее: per-user customization секций digest'а (tracker on/off, wiki updates on/off) — отдельное решение.

## Перенос в ADR

- [x] delivery-часть (HTML parse_mode, TL;DR-секция, `<b>`-section split + `(n/m)`, `send_document` fallthrough, `data/runs/` persist, `notify_policy=always` empty-line) перенесена в [`docs/adr/ADR-024-digest-presentation.md`](../../adr/ADR-024-digest-presentation.md) (2026-05-12, bd `aisw-w3k`).
- [ ] interactive-часть (actionable ±2h cards, `/expand`, per-user section toggles) — за bd `aisw-269` (Phase-D.b.2b), будущий ADR.
