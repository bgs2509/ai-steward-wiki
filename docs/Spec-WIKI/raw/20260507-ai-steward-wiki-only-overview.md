# AI Steward Wiki-Only — описание проекта

**Дата:** 2026-05-07
**Назначение:** отдельный, изолированный сервис на отдельной машине. Только WIKI-режим. На основе метода Андрея Карпаты

---

## 1. Назначение

Мультипользовательский Telegram-сервис на VPS, который превращает Claude Code CLI в персонального WIKI-ассистента для каждого юзера. Пользователь общается с ботом естественным языком (текст, файлы, фото, видео, голос), без явного указания папок и команд — бот сам определяет автора по `telegram_id`, классифицирует контент, выбирает целевую WIKI-папку юзера и запускает Claude в нужной сессии. Сервис обеспечивает:

1. **Идентификация юзера** — определение Telegram user ID → home_dir в `USERS/<NAME>/` (см. §3a, §7.1).
2. **Двухступенчатый запуск Claude** — первый вызов = классификатор (выбор WIKI-папки и сессии), второй = исполнитель в выбранной WIKI (см. §2).
3. **Автосоздание WIKI-папок** — если классификатор решает, что нужна новая WIKI (например, `Travel-WIKI`), бот создаёт её со всеми артефактами LLM Wiki (`CLAUDE.md`, `index.md`, `log.md`, `raw/`).
4. **Karpathy LLM Wiki**-режим в каждой папке с `WIKI` в имени — Claude работает как «строгий библиотекарь» и поддерживает структурированную базу знаний в Markdown.
5. **Управление расписанием через Telegram** — создание/редактирование/удаление cron-задач (reminder, wiki, digest — см. §8.3.2).

## 2. Сценарии использования

Базовый поток — **мультипользовательский, безкомандный**: юзер кидает в TG что угодно (промпт, файл, фото, видео, голос), бот делает всё остальное.

Инструмент LINT запускается один раз в сутки в 3 утра по UTC через CRONE в LINUX в каждой ВИКИ папке и быстро работает в фоне
### 2.1. Базовый поток (двухступенчатый запуск)

1. **Идентификация.** Бот (aiogram) принимает сообщение, определяет автора по `telegram_id` → находит его `home_dir = USERS/<NAME>/` в allowlist (§7.1). Неизвестный ID игнорируется молча.
2. **Сохранение входа.** Текст/файл/медиа сохраняется в `home_dir` (типично — в `Inbox-WIKI/raw/<timestamp>_<source>.<ext>`, см. §8.3.1).
3. **Первый промпт — Классификатор.** Бот запускает Claude (или fast-path Haiku, см. §8.3.3) с промптом-классификатором, который читает:
   1. текущее сообщение юзера,
   2. недавнюю историю промптов юзера,
   3. список существующих WIKI-папок в `home_dir`.

   Классификатор решает:
   1. **в какой WIKI-папке** будет идти работа (`Health-WIKI`, `Expenses-WIKI`, `Travel-WIKI`, …),
   2. **какую сессию Claude** использовать — продолжить существующую (`--resume <session_id>`) или начать новую,
   3. если подходящей WIKI **не существует** — бот создаёт новую `<Domain>-WIKI/` со всеми артефактами LLM Wiki (`CLAUDE.md` из шаблона, `index.md`, `log.md`, `raw/`) внутри `home_dir`.
4. **Второй промпт — Исполнитель.** Бот запускает Claude в выбранной WIKI-папке (`cwd=<wiki>`, `--add-dir <wiki>`, инжект LLM Wiki system prompt) с пользовательским промптом. Claude следует инструкциям `CLAUDE.md` этой WIKI (схема Karpathy: ingest / query / lint).
5. **Стриминг ответа в TG.** Вывод Claude транслируется обратно автору; список изменённых страниц + последняя запись `log.md` идут отдельным итоговым сообщением.

### 2.2. Прочие режимы

1. **Явный `/run <wiki-path> <prompt>`** — power-user / admin минует классификатор и указывает WIKI напрямую (§6).
2. **Cron-режим** — три типа задач (см. §8.3.2):
   1. `reminder_job` — TG-сообщение по расписанию без Claude.
   2. `wiki_job` — Claude в одной WIKI с фиксированным промптом (например, `daily ingest`).
   3. `digest_job` — Claude с `--add-dir` в несколько WIKI + сводка в TG.
3. **Inline-подтверждения.** Классификатор может задать уточняющий вопрос («*Положу в `Travel-WIKI`. Напомнить за 24ч до вылета?*») с inline-кнопками — после ответа юзера запускается Исполнитель и/или создаётся cron-задача.

> Запуск Claude **вне WIKI-папок запрещён** — путь, в имени которого нет `WIKI`, отклоняется ботом с ошибкой `NotAWikiPath` (§5). Anti-nesting check (§5 п.2) запрещает вложенные WIKI.

## 3. Архитектура (черновик)

```
ai-steward-wiki/
├── docs/                       # описания, ADR, спецификации
├── src/
│   ├── bot/                    # aiogram 3.x — Telegram интерфейс
│   ├── runner/                 # запуск claude CLI через asyncio.subprocess
│   ├── scheduler/              # APScheduler / системный cron, CRUD через TG
│   ├── wiki/                   # детектор WIKI-папок, инжект системного промпта
│   └── config/                 # pydantic-settings (.env)
├── data/
│   └── jobs.db                 # SQLite — состояние cron-задач
└── tests/
```

**Стек:**
1. Python 3.11+, `uv`
2. `aiogram` 3.x — Telegram
3. `claude` CLI — запускается через `asyncio.create_subprocess_exec`
4. `APScheduler` (cron-стиль триггеры) либо системный crontab через python-crontab
5. `pydantic-settings`, `SQLAlchemy` 2.x async + SQLite
6. systemd unit `ai-steward-wiki.service`

## 3a. Структура рабочих папок на VPS

Корень workspace бота (например, `/home/bgs/ai-steward-wiki-workspace/`) содержит **только** life-зону юзеров. Папка `ADMIN/` отсутствует — у админа нет личной WIKI-зоны на этой машине, он администрирует чужие WIKI.

```
ai-steward-wiki/
└── USERS/                      # life-зона: личные папки юзеров (sandbox)
    ├── GENA/                   # home_dir Геннадия — содержит ТОЛЬКО WIKI-папки + профиль
    │   ├── CLAUDE.md           # профиль юзера (один на life-зону)
    │   ├── Health-WIKI/        # домен: здоровье
    │   ├── Recipes-WIKI/       # домен: кулинария
    │   ├── Study-WIKI/         # домен: учёба
    │   ├── Schedule-WIKI/      # домен: расписание
    │   └── Expenses-WIKI/      # домен: расходы
    ├── TANIA/                  # home_dir Татьяны
    │   ├── CLAUDE.md
    │   ├── Health-WIKI/
    │   ├── Recipes-WIKI/
    │   └── Schedule-WIKI/
    └── DARI/                   # home_dir Дари
        ├── CLAUDE.md
        └── ...
```

### Правила доступа

1. **`USERS/<NAME>/`** — `home_dir` для соответствующего Telegram user-аккаунта. user-роль не может выйти за пределы своей папки (см. §7.2).
2. Внутри `USERS/<NAME>/` юзер организует домены **только как параллельные WIKI-папки** (siblings). Запуск Claude разрешён только в папках с `WIKI` в имени.
3. **Параллельная модель доменов (sibling-only):** каждый домен — самостоятельный артефакт `<Domain>-WIKI/` непосредственно внутри `USERS/<NAME>/`. Примеры: `Health-WIKI`, `Recipes-WIKI`, `Study-WIKI`, `Schedule-WIKI`, `Expenses-WIKI`. Юзер растёт горизонтально (новые siblings), а не вертикально.
4. **Запрет вложения WIKI в WIKI (anti-nesting).** Папка с `WIKI` в имени не может находиться внутри другой папки с `WIKI` в имени. Бот при `/run`, `/wiki_init` и при cron-запуске идёт от cwd вверх до `home_dir`; если хоть один ancestor содержит `WIKI` в имени — запрос отклоняется ошибкой `NestedWikiNotAllowed`. Причины: конфликты `raw/` (двойной ingest), `index.md`/`log.md` (двойной SSoT), `CLAUDE.md` (смешение схем разных доменов), неоднозначность детектора, ломающиеся бэклинки.
5. **Подкатегории внутри одной WIKI — обычные папки, не WIKI.** Внутри `Health-WIKI/` допустимы `entities/`, `concepts/`, `cardio/`, `labs/` и т.п. У них **нет** своих `raw/`/`index.md`/`log.md`/`CLAUDE.md` и `WIKI` в имени. Один артефакт на домен.
6. **Кросс-доменные запросы.** Автоматические кросс-доменные ingest запрещены (ломают изоляцию). Допустимы: (a) ручной запуск Claude в одной WIKI с явной read-only ссылкой через `--add-dir` на соседнюю; (b) отдельная meta-WIKI (`Crosslinks-WIKI/`) как ещё один sibling.
7. Корневой путь workspace задаётся в конфиге (`WORKSPACE_ROOT`); все проверки путей делаются относительно него.

## 4. WIKI-режим (по Karpathy)

Источник: [Karpathy LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) + [твит](https://x.com/karpathy/status/2039805659525644595).

### Трёхслойная архитектура

1. **Raw Sources** (`raw/`) — неизменяемые исходники: PDF, статьи, картинки, выписки. LLM только читает.
2. **Wiki** — Markdown-страницы, которые **генерирует и поддерживает только LLM**: саммари, entity-страницы, концепт-страницы, перекрёстные ссылки.
3. **Schema** — `CLAUDE.md` / `AGENTS.md` в корне папки. Конституция вики: правила структуры, конвенции именования, какие таблицы делать, как сохранять картинки и т.п.

### Обязательные файлы

1. `CLAUDE.md` (или `AGENTS.md`) — схема/конституция конкретной вики.
2. `index.md` — каталог всех страниц с однострочным описанием, сгруппированный по категориям. Обновляется на каждом ingest.
3. `log.md` — append-only хронологический лог действий LLM, парсируемые префиксы вида `## [YYYY-MM-DD] ingest | Title`.

### Три ключевые операции

1. **Ingest** — обработка новых файлов из `raw/` по одному: саммари → обновление entity/concept страниц → запись в `log.md` и `index.md`.
2. **Query** — поиск по существующим страницам, синтез ответа с цитатами, **сохранение ответа обратно** в вики как новой страницы (петля знаний).
3. **Lint** — периодический аудит: противоречия между старыми/новыми статьями, осиротевшие страницы без бэклинков, устаревшие утверждения, пробелы для догугливания.

### Ключевая идея

Вики — это **persistent, compounding artifact**. Каждый запрос пользователя не просто читается и забывается, а конденсируется обратно в файлы. База растёт и становится умнее с каждой итерацией. Не нужен RAG / векторная БД — достаточно файловой системы и дисциплинированного агента. Obsidian используется как читалка, не редактор.

## 5. Контракт детектора WIKI-папок

1. Проверка: `"WIKI" in basename(cwd).upper()`. Если нет — запуск **отклоняется** (`NotAWikiPath`, сервис работает только в WIKI-режиме).
2. **Anti-nesting check.** От cwd вверх до `home_dir` ни один ancestor не должен содержать `WIKI` в имени. Если хоть один содержит — запуск отклоняется ошибкой `NestedWikiNotAllowed`. Один домен = одна WIKI = один артефакт; вложенные вики порождают конфликты `raw/`, `index.md`, `log.md`, `CLAUDE.md` и детектора (см. §3a п.4).
3. Перед запуском Claude бот:
   1. Убеждается, что в cwd есть `CLAUDE.md` (если нет — генерирует базовый из шаблона LLM Wiki).
   2. Убеждается, что есть `index.md` и `log.md` (создаёт пустые с заголовками).
   3. Убеждается, что есть подпапка `raw/`.
   4. Префиксует пользовательский промпт системной инструкцией LLM Wiki.

## 6. Telegram-команды (черновик)

1. `/run <wiki-path> <prompt>` — разовый запуск Claude в WIKI-папке.
2. `/cron_add <cron-expr> <wiki-path> <prompt>` — добавить периодическую задачу.
3. `/cron_list` — список задач.
4. `/cron_del <id>` — удалить задачу.
5. `/cwd <wiki-path>` — установить дефолтную WIKI-папку для текущего чата.
6. `/wiki_init <path>` — принудительно инициализировать LLM Wiki в папке (создать `CLAUDE.md`, `index.md`, `log.md`, `raw/`). Имя папки должно содержать `WIKI`.

## 7. Безопасность и роли пользователей

### 7.1. Модель ролей (multi-tenant Telegram)

Бот обслуживает несколько Telegram-аккаунтов одновременно. Каждый Telegram user ID имеет роль:

1. **`admin`** — администрирование сервиса.
   1. Может запускать Claude в **любой WIKI-папке** на сервере (но не вне WIKI).
   2. Может управлять cron-задачами всех пользователей.
   3. Может выполнять `/wiki_init` в любой папке внутри `USERS/<NAME>/`.
   4. Личной life-зоны (`USERS/ADMIN/`) на этой машине нет.

2. **`user`** — sandbox-доступ только к своей домашней папке.
   1. Каждому `user`-аккаунту назначен **`home_dir`** = `USERS/<NAME>/`.
   2. **Запрещён выход выше** `home_dir`: любой путь, который после `realpath`-нормализации не начинается с `home_dir`, отбрасывается.
   3. Запуск Claude разрешён **только в WIKI-подпапках** внутри `home_dir`.
   4. Все cron-задачи юзера привязаны к WIKI-папкам внутри его `home_dir`.

### 7.2. Защита от path traversal

1. Раскрытие `~`, переменных окружения, относительных компонент через `Path(p).expanduser().resolve(strict=False)`.
2. Проверка `resolved_path.is_relative_to(home_dir)` для user-роли (для admin — `is_relative_to(WORKSPACE_ROOT/USERS)`).
3. Запрет символических ссылок наружу: `realpath` → повторная проверка.
4. Дополнительно: `"WIKI" in resolved_path.name.upper()` — обязательно для всех команд `/run` и `/cron_add`.
5. Любой нарушающий запрос — отвергнут, событие записано в audit-лог.

### 7.3. Запуск Claude с ограничениями

1. Claude CLI запускается с `cwd=<wiki-path>` и **`--add-dir`** только в пределах этой WIKI-папки.
2. Пермишен-режим: `acceptEdits` для md-файлов внутри WIKI; `--dangerously-skip-permissions` — никогда автоматически.
3. Опциональная систем-изоляция: отдельный systemd unit per user с `ProtectHome=`, `ReadWritePaths=<home_dir>`, `PrivateTmp=true`.

### 7.4. Прочее

1. Allowlist Telegram user ID — неизвестные ID игнорируются молча.
2. Секреты (`TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`) — только в `.env`, не в git.
3. Pre-commit `gitleaks` для защиты от утечек.
4. Audit-лог всех команд (user_id, ts, command, cwd, prompt-hash) в SQLite.

### 7.5. Пример конфига ролей

```toml
# config/roles.toml
[[users]]
telegram_id = 111111111
role = "admin"
name = "Геннадий"

[[users]]
telegram_id = 222222222
role = "user"
name = "Татьяна"
home_dir = "/home/bgs/ai-steward-wiki-workspace/USERS/TANIA"

[[users]]
telegram_id = 333333333
role = "user"
name = "Дари"
home_dir = "/home/bgs/ai-steward-wiki-workspace/USERS/DARI"
```

## 7a. Профиль запуска Claude (только WIKI)

В этом сервисе доступен **единственный** профиль — `wiki`. Любой другой путь отклоняется до запуска.

### Алгоритм

1. Определить роль вызывающего TG-аккаунта (`admin` / `user`) и проверить доступ к `cwd` (см. §7.2).
2. Проверить: `"WIKI" in basename(cwd).upper()`. Если нет — отклонить запрос.
3. Подготовить запуск под профиль `wiki`.

### Параметры профиля `wiki`

| Аспект | Значение |
|---|---|
| **Кто может запускать** | владелец `USERS/<NAME>/` + admin |
| **Что читает Claude (CLAUDE.md цепочка)** | глобальный `~/.claude/CLAUDE.md` + `USERS/<NAME>/CLAUDE.md` (профиль юзера) + `<wiki>/CLAUDE.md` (схема LLM Wiki) |
| **Системный промпт (инжект ботом)** | **LLM Wiki system prompt** (Karpathy) — режим строгого библиотекаря |
| **Авто-инициализация файлов** | `CLAUDE.md`, `index.md`, `log.md`, `raw/` — создаются если отсутствуют |
| **Workflow и skills** | wiki-операции `ingest` / `query` / `lint`. Никаких feature-workflow / GRACE / TDD — это не dev-режим |
| **Документация SSoT** | `index.md` + `log.md` (контент-центрик) |
| **Pre-commit / git** | git опционально; если есть — только `gitleaks` (защита приватных данных) |
| **`--permission-mode`** | `acceptEdits` для md-файлов, write ограничен текущей wiki-папкой |
| **`--add-dir`** | только сама `<wiki>` папка |
| **Запреты на чтение** | `.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials.*` + чужие `USERS/*` папки |
| **Бот-хуки до запуска (PreRun)** | проверка/создание `CLAUDE.md`/`index.md`/`log.md`/`raw/`; инжект Wiki system prompt; audit-лог |
| **Бот-хуки после запуска (PostRun)** | обновить timestamp последнего ingest/lint; если в `log.md` появился `## [date] lint` — выслать summary противоречий |
| **Cron-семантика** | `daily ingest raw/`, `weekly lint`, `monthly export` |
| **Тон ответов в TG** | библиотекарский, со ссылками на страницы вики |
| **Audit-лог** | команда + список изменённых страниц |

### Псевдокод

```python
def resolve_profile(cwd: Path, role: Role, home_dir: Path) -> WikiProfile:
    cwd = cwd.resolve()
    if not is_allowed(cwd, role):
        raise AccessDenied
    if "WIKI" not in cwd.name.upper():
        raise NotAWikiPath(cwd)
    # anti-nesting: ни один ancestor до home_dir не должен быть WIKI
    for parent in cwd.parents:
        if parent == home_dir or parent == home_dir.parent:
            break
        if "WIKI" in parent.name.upper():
            raise NestedWikiNotAllowed(cwd, parent)
    return WikiProfile(cwd)

def launch(profile: WikiProfile, prompt: str):
    profile.ensure_files()                   # CLAUDE.md, index.md, log.md, raw/
    profile.audit_log("run", prompt)
    cmd = profile.build_claude_cmd(prompt)   # --add-dir, --permission-mode, system-prompt inject
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=profile.cwd)
    stream_to_telegram(proc)
    await proc.wait()
    profile.post_run_summary(proc.returncode)  # diff pages, last log.md entry
```

### Пример: `USERS/GENA/Health-WIKI`

1. Бот: проверяет/создаёт `CLAUDE.md` (LLM Wiki schema), `index.md`, `log.md`, `raw/` → audit-log → инжектит **LLM Wiki system prompt** → запускает `claude --add-dir USERS/GENA/Health-WIKI --permission-mode acceptEdits` в этой папке.
2. Claude читает: `~/.claude/CLAUDE.md` + `USERS/GENA/CLAUDE.md` + `Health-WIKI/CLAUDE.md`.
3. Claude работает как библиотекарь: читает `raw/` по одному файлу, обновляет entity/concept страницы, дописывает `index.md` и `log.md`.
4. После завершения — бот шлёт в TG список новых/изменённых страниц + последнюю запись `log.md`.

## 8. Расширенные сценарии и proactive life-assistant (new findings)

> Уточнение от 2026-05-07: ключевая UX-фича сервиса — не только запуск Claude в WIKI, но и **проактивная работа со входящим контентом из TG**. Это меняет архитектуру сервиса с «runner для WIKI-папок» на **life-assistant с inbox-ом, NL-командами, smart-classifier и тремя типами cron-задач**.

### 8.1. Три класса пользовательских сценариев

1. **Reminder-as-message (lightweight cron)** — пример: «*разбуди меня в 6 утра завтра*». Cron-задача шлёт TG-сообщение пользователю. **Claude CLI не запускается** — это просто scheduler + sendMessage.
2. **Aggregator / digest-задачи** — пример: «*каждый день в 9 утра проверяй все WIKI, planner-ы, базы и давай сводку по делам, встречам, платежам*». Cron запускает Claude с `--add-dir` сразу в несколько WIKI + читает все `planner.json`, агрегирует, шлёт сводку в TG.
3. **Smart inbox + auto-routing** — пример: пользователь кидает в TG график платежей по кредитам / театральную афишу / скан авиабилета. Claude:
   1. классифицирует контент,
   2. выбирает целевую WIKI,
   3. **сам спрашивает** «*напомнить за 3 дня до платежа? за неделю до полёта?*»,
   4. при подтверждении — кладёт файл в нужную WIKI, делает ingest, создаёт cron-задачи.

### 8.2. Дополнительные жизненные сценарии (use-cases backlog)

1. **Медицина** — рецепт лекарства → напоминать пить N раз/день M дней; запись на анализы → напомнить за день; результаты анализов → подшить в `Health-WIKI` + флажок отклонений.
2. **Финансы** — чек из магазина (фото) → OCR → `Expenses-WIKI`; зарплата → `Expenses-WIKI/income`; подписка (Netflix) → напоминание за 3 дня до списания.
3. **Семья** — ДР родственника → ежегодный cron; школьное расписание ребёнка → утренняя сводка; справки → `Family-WIKI`.
4. **Документы** — загранпаспорт → за 6 мес до истечения; виза, права, ОСАГО, ТО авто, СНИЛС.
5. **Учёба** — дедлайн курсовой/экзамен → серия напоминаний; конспект → `Study-WIKI`.
6. **Быт** — замена фильтра воды (6 мес); поверка счётчиков; ТО котла; полив растений.
7. **Подписки и контракты** — аренда квартиры, мобильный тариф, домен, SSL.
8. **Путешествия** — бронь отеля → check-in напоминание; страховка → купить за 3 дня; погода в день вылета (за 24ч); регистрация на рейс.
9. **Покупки** — разовое («купи молоко»); недельный список → субботний дайджест.
10. **Трекинг здоровья** — «*давление 130/85*» → запись в `Health-WIKI/metrics/`; вес, вода, шаги.
11. **Привычки/habits** — медитация в 7 утра → ежедневный пинг + tracking.
12. **Социалка** — «*созвон с Лёшей чт 18:00*» → calendar-style напоминание за 15 мин.
13. **Goal-tracking** — долгосрочные цели → еженедельная проверка прогресса.

### 8.3. Архитектурные идеи для реализации

#### 8.3.1. Inbox-папка + Router-агент

Внутри каждого `USERS/<NAME>/` появляется **`Inbox-WIKI/`** — единая точка входа:

1. Пользователь шлёт **что угодно** (текст, фото, PDF, голос) в TG без явной команды и без указания папки.
2. Бот складывает в `Inbox-WIKI/raw/<timestamp>_<source>.<ext>`.
3. Триггерится **Router-промпт** — Claude в `Inbox-WIKI/` классифицирует:
   1. тип контента (рецепт / счёт / билет / расписание / заметка / NL-команда-напоминание),
   2. целевая WIKI (`Health-WIKI`, `Expenses-WIKI`, `Travel-WIKI`, `Schedule-WIKI`),
   3. предлагаемые действия с inline-кнопками: «напомнить?», «положить в WIKI X?».
4. Claude отвечает в TG: «*Это похоже на авиабилет SVO→IST 15.06. Положу в `Travel-WIKI`. Напомнить за 24ч до вылета? за 3ч?*»
5. После подтверждения — Claude перемещает файл в целевую WIKI, делает ingest, создаёт cron-задачи.

Это закрывает вопрос «*в какую папку кидать*» — всегда в Inbox, дальше Claude разбирается.

#### 8.3.2. Унифицированный Job-объект (3 типа)

В `data/jobs.db` — одна таблица `jobs` с дискриминатором `kind`:

1. **`reminder_job`** — шлёт TG-сообщение. Без Claude. Поля: `chat_id`, `message`, `cron_expr`, `lead_time`.
2. **`wiki_job`** — запускает Claude в одной WIKI с промптом (текущий §6 `/cron_add`).
3. **`digest_job`** — Claude с `--add-dir` в несколько WIKI + чтение всех `planner.json`, сводка → TG.

Все три создаются одним APScheduler, отличаются action-handler-ом.

#### 8.3.3. Двухуровневый intent-detection

Чтобы не запускать тяжёлый Claude CLI на каждое «*разбуди в 6*»:

1. **Fast path** — лёгкая LLM (Haiku напрямую через Anthropic API) или regex для тривиальных случаев → классифицирует intent (`reminder` / `wiki_action` / `unclear`).
2. Если intent = `reminder` → создать `reminder_job` напрямую (миллисекунды).
3. Если intent = `wiki_action` или `unclear` → положить в Inbox, запустить Router-Claude (CLI).

Экономит токены и latency на 80% типичных сообщений.

### 8.4. Влияние на исходные разделы документа

1. **§6 команды** — `/run` и `/cron_add` остаются для admin/power-user, но 90% UX = просто кидать сообщения без команд.
2. **§3a структура папок** — добавляется обязательная `Inbox-WIKI/` per-user; роль = триаж/router (отличается от domain-WIKI = librarian).
3. **§7.1 роли** — нужен новый action-type `send_telegram_message` с проверкой что recipient = сам user (анти-спам).
4. **`planner.json` интеграция** — digest-job должен читать `planner.json` из основного `ai-steward` (cross-service) ИЛИ planner мигрирует внутрь WIKI как SSoT.
5. **Шаблоны `CLAUDE.md`** — два класса: (a) **`Inbox-WIKI/CLAUDE.md`** — router/triage; (b) **`<Domain>-WIKI/CLAUDE.md`** — librarian (Karpathy).

---

## 9. Открытые вопросы (отсортированы по критичности)

> Принцип сортировки: архитектурные развилки → данные/контракты → роутинг/UX → безопасность → эксплуатация. Закрыть нужно сверху вниз; нижние вопросы могут быть переписаны решениями верхних, поэтому преждевременный ответ на низ — потеря.

### Tier A — архитектурные развилки (блокируют MVP-план)

1. **Job-модель: 3 типа в одной таблице vs отдельные таблицы.** Дискриминатор `kind` (reminder/wiki/digest) или три модели? Влияет на `data/jobs.db` schema и весь scheduler-модуль.
2. **Scheduler backend.** APScheduler + SQLAlchemyJobStore (SQLite) vs системный crontab vs гибрид. (Бывший §8.1.)
3. **Inbox-WIKI: per-user vs global.** `USERS/<NAME>/Inbox-WIKI/` (изоляция) vs глобальный `INBOX/` с роутингом по `chat_id` (проще).
4. **Auto-classification engine.** Claude CLI в Inbox-WIKI (дорого, медленно, ~10-30 сек) vs прямой Anthropic API call с Haiku (быстро, дёшево, ~1-2 сек) vs гибрид (Haiku triage → CLI ingest).
5. **NL-парсинг времени.** Локальный (`dateparser`, `parsedatetime`) vs LLM-парсинг vs гибрид. Часовой пояс юзера в `roles.toml` обязателен.
6. **Planner.json SSoT.** Читать из основного `ai-steward` (cross-service coupling) vs мигрировать planner внутрь каждой WIKI vs дублировать (drift-риск).
7. **Конкурентные запуски Claude.** Один процесс на чат / на WIKI / глобальная очередь / пул с лимитом. (Бывший §8.3.) Особенно критично при digest-job + concurrent inbox.
8. **Lock на запись в WIKI.** Что если cron-digest и `/run` одновременно пишут в одну WIKI? Lock-файл per-wiki / очередь / optimistic.

### Tier B — контракты данных и роутинга

9. **Шаблон `CLAUDE.md` для Inbox-WIKI.** Содержание router-промпта: список доменов, правила классификации, формат ответа в TG.
10. **Шаблон `CLAUDE.md` для domain-WIKI.** Единый дефолт vs per-domain (Health/Recipes/Study). (Бывший §8.4.)
11. **Идемпотентность ingest.** Как избежать дубля напоминаний если юзер кинул один и тот же билет дважды? Hash-чек контента / dedup-окно / явное «уже видел».
12. **Подтверждение действий в TG.** Inline-кнопки да/нет (структурно) vs free-form ответ vs оба.
13. **Digest-формат ответа.** Plain text / markdown / structured cards с кнопками.
14. **Голосовые и фото-вход.** STT (faster-whisper) и OCR (pytesseract) — обязательны для inbox-сценария или text-only MVP?
15. **Стриминг Claude → TG.** Chunked-edit одного сообщения vs serial-сообщения; политика при rate-limit 1 msg/sec. (Бывший §8.2.)
16. **Размер вывода Claude.** Лимит на стриминг в TG (4096 chars), обрезка, сохранение полного лога куда (отдельный файл в WIKI? `data/runs/`?).
17. **Таймауты и kill-policy.** Макс. длительность одного `/run`, поведение при зависании, прерывание из TG (`/cancel`).
18. **Cron-результат без активного чата.** Куда слать результат cron-запуска (тот же `chat_id` владельца? админу при ошибке?).
19. **Failure mode для cron.** Retry-политика, уведомление при падении, dead-letter queue.

### Tier C — Claude CLI runtime

20. **Аутентификация Claude CLI.** `ANTHROPIC_API_KEY` per-process / subscription auth / `claude login`. Изоляция `~/.claude/` между юзерами на одном хосте.
21. **Инжект LLM Wiki system prompt.** `--system-prompt` / `--append-system-prompt` / через `CLAUDE.md` / stdin. SSoT текста промпта (файл в репо `prompts/wiki.md`?).
22. **`--add-dir` область.** Только сама WIKI или включая `USERS/<NAME>/CLAUDE.md` (для чтения профиля юзера).
23. **Регистр и формат `WIKI`-маркера в имени.** §5 `.upper()` — substring или suffix `-WIKI`? Точное правило/regex.
24. **Anti-nesting граница для admin.** Псевдокод §7a останавливается на `home_dir.parent`; для admin `home_dir` нет — какая граница?

### Tier D — UX и роли

25. **Доступ admin к чужим `USERS/`.** Read-only / full-run / запрет. (Бывший §8.6.)
26. **`/wiki_init` авторизация.** Кто создаёт новые `<Domain>-WIKI/` — user сам или только admin? Лимит количества WIKI на юзера.
27. **Onboarding нового юзера.** TG-flow (`/start` → admin одобряет) vs ручное редактирование `roles.toml`.
28. **Allowlist hot-reload.** `roles.toml` — hot-reload или restart-only? Кто правит — admin через TG или вручную на VPS.
29. **Multi-language.** Интерфейс бота — ru/en, определяется per-user в `roles.toml`.
30. **История чата.** Журнал команд/ответов в SQLite сверх audit-лога §7.4. (Бывший §8.5.)

### Tier E — эксплуатация и безопасность

31. **Per-user systemd unit (§7.3 п.3).** В MVP или out-of-scope.
32. **Хранилище состояния.** Одна общая SQLite `data/jobs.db` или раздельные БД (jobs/audit/sessions). Миграции — Alembic.
33. **Audit-лог PII.** Prompt-hash или plaintext. Срок хранения.
34. **Логирование сервиса.** Stdout → journald / structlog → файл / отдельный observability стек.
35. **Тестирование.** Мок Claude CLI (fake binary в `PATH`) для unit; интеграция с реальным CLI — в CI или только локально.
36. **Backup WIKI-папок.** В scope сервиса (rsync/borg) или ответственность админа VPS.
37. **Git внутри WIKI-папок.** §7a «git опционально» — авто-commit после каждого ingest/query или нет.
38. **Schema эволюция `CLAUDE.md`.** Авто-миграция существующих WIKI при обновлении шаблона или только новые.
39. **Format даты в `log.md`.** UTC или локальная TZ VPS (Europe/Moscow).

---

## 10. Следующие шаги

1. Закрыть вопросы Tier A (1–8) — без них MVP-план будет переписан.
2. Закрыть Tier B (9–19) — определяют контракты модулей.
3. Запустить `feature-workflow` в репозитории сервиса: `bd create` → Discovery → Brainstorming → GRACE Plan → Writing Plans.
4. MVP scope: `/run` + детектор WIKI + базовый `Inbox-WIKI` router (text-only) + `reminder_job` (без digest, без OCR/STT) + один тестовый юзер.
5. Iter-2: `digest_job`, OCR/STT, multi-user, `wiki_job` cron.
6. Iter-3: per-user systemd isolation, backup, advanced UX.

## 11. Источники

1. Karpathy LLM Wiki gist — https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
2. Karpathy tweet — https://x.com/karpathy/status/2039805659525644595
3. Obsidian — https://obsidian.md/download
/