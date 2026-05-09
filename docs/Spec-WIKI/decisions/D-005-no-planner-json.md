# D-005: Никакого `planner.json` — только `jobs.db` как единственный SSoT расписаний

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-06](../questions/Q-A-06-planner-ssot.md), [D-002](D-002-job-model-storage.md), [D-003](D-003-scheduler-backend.md)

## Проблема

Внутри `ai-steward-wiki` нужно решить, существует ли в файловой системе сервиса какой-либо `planner.json` (per-user / per-WIKI / экспорт), или вся семантика «когда юзер что-то делает» закрывается одной таблицей `jobs` (D-002).

## Варианты

1. **A. Per-user `USERS/<NAME>/planner.json` + `jobs.db`** — два store, two writers, drift.
2. **B. Per-WIKI `<Domain>-WIKI/planner.json` + `jobs.db`** — фрагментация SSoT по доменам.
3. **C. Только `jobs.db`, без файла `planner.json` вообще.**

## Выбор

**Вариант C, в строгой форме: `planner.json` НЕ существует в сервисе ни в каком виде.** Юзер подтвердил 2026-05-08 явной формулировкой «NO planner.json AT ALL. DELETE IT».

Обоснование:
1. Один SSoT — `jobs.db` (D-002).
2. APScheduler (D-003) уже работает с `jobs.db` нативно.
3. Любой файловый дубликат планировщика создаёт drift и two-writers anti-pattern.
4. Граница с TG-ботом `ai-steward` (см. CLAUDE.md §1.1) исключает наследование формата `planner.json` оттуда. Этот сервис проектируется без оглядки на чужой формат.

## Последствия

1. **В файловой системе `ai-steward-wiki` нет ни одного `planner.json`.** Ни per-user, ни per-WIKI, ни в `templates/`, ни как export. Запрет — load-bearing.
2. Все planner-семантичные item'ы (разовые напоминания, periodic reminders, mandatory check-ins, tracker surveys, boundary messages) живут как строки в `jobs` (D-002) с соответствующим `kind` и `payload`.
3. **Переписать упоминания в документах:**
   1. Overview §2.2 / §8.3.2 / §8.4 п.4 (текст «*читает все `planner.json`*») — при переносе в финальные artefacts заменить на «*читает items из `jobs.db`*». Сам overview в `raw/` — неизменяем (источник), пометка вносится в `research/overview-2026-05-07.md` и в design-документах при переносе.
   2. `entities/time-tracker.md` (текущий текст ссылается на `planner.json`) — обновить, заменить на ссылки на `jobs.db` / kinds D-002.
   3. `entities/job-model.md` — добавить явный пункт «`planner.json` отсутствует, см. D-005».
4. CRUD planner-семантики — через TG-команды (`/cron_add`, `/remind`, `/list`) и/или conversational UX. Файлового интерфейса нет.
5. Если когда-нибудь понадобится human-readable дамп — это **отдельное решение**, не возврат к `planner.json`. По умолчанию: `sqlite3 jobs.db ".dump"` или специальная админская CLI-команда.
6. Q-A-06 закрывается этим решением.

## Запреты (load-bearing)

1. Не создавать файлы с именем `planner.json` нигде в дереве сервиса.
2. Не вводить read-only «экспорт» в `planner.json` — это лишняя поверхность для drift.
3. Не закладывать совместимость с форматом `planner.json` других сервисов (граница CLAUDE.md §1.1).
4. Любое предложение «давай ещё JSON-зеркало» = автоматически НЕ-вариант, поднимать как новый ADR с явным обоснованием отмены D-005.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-no-planner-json.md` при финализации.
