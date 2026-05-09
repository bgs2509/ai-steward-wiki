# Q-B-13: Digest-формат ответа

**Tier:** B
**Источник:** [overview §9 п.13](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Plain text / markdown / structured cards с кнопками.

## Варианты

1. **A. Plain text.** Просто, читается на любом клиенте.
2. **B. Markdown (TG MarkdownV2).** Жирный/курсив/код. Минусы: escape-минное-поле.
3. **C. Structured cards с inline-кнопками.** Каждый пункт = карточка с действием («отметить done», «отложить»).

## Решение

- [x] **Вариант D** — hybrid: summary HTML + actionable cards только для critical:
  - **Parse mode:** HTML (`<b>`, `<i>`, `<code>`, `<a>`) — проще escape, чем MarkdownV2, согласован с aiogram-best-practice.
  - **Summary message:** одна структура с секциями, разделёнными `<b>` headers: `📅 Сегодня` / `💊 Лекарства` / `📈 Tracker` / `📝 Wiki updates`. Списки вместо таблиц (parent-`CLAUDE.md` правило).
  - **Actionable cards:** отдельные сообщения с inline-кнопками **только** для items, требующих действия в окне ±2h (medication-due-now, event-soon, pending_confirmation). Кнопки: «✅ Done» / «⏰ Snooze» / «✏️ Edit» — синхронизированы с [D-023](../decisions/D-023-tg-confirmations.md).
  - **Read-only items** (на завтра, статистика) — в summary, без кнопок.
  - **TL;DR** в первой секции (3–5 строк) — для скан-чтения с мобильного.
  - **Длинный digest (>4096):** split по секциям; границы — `<b>` header.
  - **`/expand <section>`** — показать детали (deferred-content).
- [x] оформлено как [D-024](../decisions/D-024-digest-format.md)

## Связанные

1. [Job-model](../entities/job-model.md) (digest_job)
