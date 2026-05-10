# Time Tracker

**Тип:** entity (фасад)
**Статус:** draft
**Источники:** [overview](../raw/20260507-ai-steward-wiki-only-overview.md), диалог с юзером 2026-05-08

## Суть

UX-фича поверх существующих кирпичей (`jobs.db` + `job-model` + `classifier`): Клод периодически спрашивает «что делал», предлагает 3 предсказания в виде инлайн-кнопок, помнит паттерны по дням недели, напоминает об обязательных делах и проверяет «сделал ли». Не отдельная подсистема — надстройка-сценарий.

## Сценарии

1. **Граница дня**
   1. 06:00 — сообщение «подъём» (фиксированная нижняя граница окна).
   2. 23:00 — сообщение «пора спать» (фиксированная верхняя граница окна).
2. **Периодический опрос «что делал?»**
   1. Будни: шаг **2 часа** в окне 06:00–23:00.
   2. Выходные: шаг **5 часов** в том же окне.
   3. К каждому опросу прикреплены **3 инлайн-кнопки** с предсказаниями (см. [predictive-replies](../concepts/predictive-replies.md)).
   4. Если в окне ±30 мин есть `jobs.db` item — общий опрос подавляется (anti-spam).
3. **Дневной план (разовый)**
   1. Юзер создаёт разовые items в `jobs.db` через conversational UX. Пример: 09:00 работа, 12:00 врач, 15:00 спорт, 20:00 домой, 22:00 спать.
   2. В момент item — обычное напоминание планировщика.
   3. Через N минут после item — follow-up «сделал?» (см. [mandatory-checkins](../concepts/mandatory-checkins.md)).
   4. Day-override бьёт schedule-profile: если на 22:00 стоит «пораньше спать», стандартное «пора спать» в 23:00 не дублируется.
4. **Обязательные дела**
   1. Категории: зарядка, еда, сон, учёба, таблетки, лекарства, диета.
   2. Логика: напоминание в момент X → follow-up «сделал?» через N минут.
   3. Детали правила — в [mandatory-checkins](../concepts/mandatory-checkins.md).
5. **Память по дням недели**
   1. Клод накапливает «по понедельникам обычно работа+спорт» и кормит этим predictive-replies.
   2. Хранение закрыто в [D-014](../decisions/D-014-tracker-memory-model.md): append-only `tracker_answers` в `jobs.db`.

## Используемые механизмы

- `jobs.db` (D-002, Flat + JSON payload) — единственный SSoT расписаний. Все items трекера — строки с соответствующим `kind` (`tracker_survey`, `tracker_followup`, `boundary_message`, `reminder_job`). См. [D-005](../decisions/D-005-no-planner-json.md): `planner.json` в сервисе не существует.
- `job-model` (entity) — общий механизм recurring jobs. Трекер описывает **конфигурацию**, не дублирует логику. См. [D-001](../decisions/D-001-time-tracker-vs-job-model.md).
- `APScheduler AsyncIOScheduler` (D-003) — исполнение триггеров, общий event-loop с aiogram.
- `classifier` (entity, TBD) — расширяется методом `predict_top3(context)` для предсказаний.

## Конфигурация (профиль юзера)

Включается через `{User}/CLAUDE.md`:
1. `tracker.enabled: true`
2. `tracker.window: "06:00-23:00"`
3. `tracker.weekday_step_min: 120`
4. `tracker.weekend_step_min: 300`
5. `tracker.mandatory: [meds, food, sleep, ...]`

## Связанные

1. [predictive-replies](../concepts/predictive-replies.md)
2. [schedule-profiles](../concepts/schedule-profiles.md)
3. [mandatory-checkins](../concepts/mandatory-checkins.md)
4. [D-001 time-tracker vs job-model](../decisions/D-001-time-tracker-vs-job-model.md)
5. [Q-A-09 tracker memory model](../questions/Q-A-09-tracker-memory-model.md)

## Открытые вопросы

1. Как определяется тип дня (будни/выходные/праздники) — внешний календарь или захардкоженный список?
2. Как юзер отвечает «другое» — свободный текст или подменю?
3. Что делать с пропущенными ответами (опрос отправлен, юзер не нажал кнопку)?
