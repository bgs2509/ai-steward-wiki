# D-001: Time Tracker — отдельная подсистема или надстройка над job-model?

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [time-tracker](../entities/time-tracker.md), диалог 2026-05-08

## Проблема

Идея «трекера времени» (опросы каждые 2/5 часов, обязательные напоминания, предсказания ответов) пересекается с уже задуманным `job-model` (общий механизм recurring jobs) и `jobs.db` (runtime-данные расписаний). Где провести границу?

## Варианты

1. **A — Отдельная подсистема `time-tracker`**
   1. Своя entity, своя таблица jobs, свой scheduler-loop.
   2. ✅ Чёткая SSoT внутри одной фичи.
   3. ❌ Дублирует `job-model` (recurring + cron-like) — нарушает DRY.
   4. ❌ Два scheduler-loop'а в боте — нарушает SSoT и SoC.
   5. ❌ Сложнее переиспользовать механизмы (predictive replies заперты в трекере).
2. **B — Надстройка-сценарий поверх `job-model` + `classifier` + `jobs.db`**
   1. Трекер = конфигурация existing-механизмов + 3 новых концепта (predictive-replies, schedule-profiles, mandatory-checkins).
   2. Никаких новых infra-сущностей.
   3. ✅ DRY и SSoT — один scheduler, один storage.
   4. ✅ Концепты переиспользуемы (predictive-replies можно прицепить к чему угодно).
   5. ❌ `job-model` нужно слегка обогатить (поддержка mandatory-флага, follow-up-delay).
   6. ❌ Логика трекера размазана по 3 концептам + фасаду — больше страниц.

## Выбор

**Вариант B (надстройка).**

Обоснование:
1. Юзер описывает **UX-фичу**, а не infra-слой. Все механики (расписание, повторы, напоминания) уже есть в `jobs.db` / `job-model`.
2. Реальное новое — три концепта (predictive replies, schedule-profiles, mandatory check-ins). Их выделение в `concepts/` делает их переиспользуемыми.
3. Cost разбиения по страницам (Cons.6) меньше, чем cost дублирования scheduler'а.

## Последствия

1. `entities/job-model.md` (когда будет создана) ДОЛЖНА поддерживать:
   1. категорию `mandatory` (= нужен follow-up).
   2. поле `follow_up_delay_min`.
2. `entities/classifier.md` ДОЛЖНА получить метод `predict_top3(slot, history) -> [reply, reply, reply]`.
3. `jobs.db` schema расширяется только через уже принятый `job-model` контракт ([D-002](D-002-job-model-storage.md)); отдельного tracker-storage нет.
4. Трекер не имеет собственного storage. Память паттернов — отдельный вопрос ([Q-A-09](../questions/Q-A-09-tracker-memory-model.md)).

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-NNN-time-tracker-architecture.md` (когда финализируется и пойдём в код)
