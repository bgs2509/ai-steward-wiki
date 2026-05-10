# Spec-WIKI — Конституция

> **Тип:** life-зона (Karpathy LLM Wiki). НЕ dev-проект.
> GRACE / feature-workflow / TDD / MODULE_CONTRACT в этой папке **не применяются**.
> Глобальные dev-правила из `~/.claude/CLAUDE.md` для кода — игнорируются здесь.

## 1. Назначение

Эта папка — **рабочая вики для проектирования сервиса `ai-steward-wiki`**: research, исследование альтернатив, наброски ТЗ, ADR-черновики, разбор open questions из `20260507-ai-steward-wiki-only-overview.md`.

### 1.1. Граница с `ai-steward` (TG-бот)

**`ai-steward-wiki` и `ai-steward` — два полностью разных сервиса.**

1. `ai-steward` — существующий TG-бот (`/home/bgs/ai-steward/`, его `CLAUDE.md`, его `planner.json`-формат, его пользователи и проекты). На этот сервис здесь **не ссылаемся** при проектировании.
2. `ai-steward-wiki` — проектируемый с нуля изолированный сервис (overview §1: «отдельный, изолированный сервис на отдельной машине»).
3. **Никаких пересечений, миграций, импортов формата, cross-service чтений между ними не закладывать.** Если в варианте всплывает «прочитать у `ai-steward`», «мигрировать `planner.json` оттуда», «использовать общий volume» — это автоматически НЕ-вариант.
4. Если когда-нибудь интеграция понадобится — **только по явному запросу юзера**, отдельным решением. Default — нулевая связь.
5. parent-`CLAUDE.md` (`/home/bgs/ai-steward/CLAUDE.md`) **не источник** для проектирования `ai-steward-wiki`. Источники — только overview в `raw/` и страницы внутри Spec-WIKI.

Это **research-слой**, не SSoT для итоговых артефактов:

1. **Research / черновики / discovery / brainstorm** → живут здесь как Markdown-страницы.
2. **Финальные dev-артефакты** (`discovery.md`, `design.md`, `plan.md`, ADR) → переносятся отсюда в `docs/superpowers/specs/`, `docs/superpowers/plans/`, `docs/adr/` уже в формате feature-workflow.

Правило **один факт — одна SSoT** соблюдается переносом, а не дублированием.

## 2. Метод (по Karpathy LLM Wiki)

Источник: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

### Трёхслойная архитектура

1. **Raw Sources** (`raw/`) — неизменяемые исходники: цитаты, документы, скриншоты, ссылки. LLM только читает.
2. **Wiki** — Markdown-страницы, которые **генерирует и поддерживает только LLM**: саммари, entity-страницы, концепт-страницы, decision-страницы, перекрёстные ссылки.
3. **Schema** — этот `CLAUDE.md`. Конституция вики.

### Три ключевые операции

1. **Ingest** — обработать новые файлы из `raw/` по одному: саммари → обновить/создать entity/concept страницы → добавить запись в `log.md` и `index.md`.
2. **Query** — найти по существующим страницам, синтезировать ответ с цитатами, **сохранить ответ обратно** как новую страницу (петля знаний).
3. **Lint** — аудит: противоречия, осиротевшие страницы без бэклинков, устаревшие утверждения, пробелы для догугливания.

## 3. Структура страниц

```
Spec-WIKI/
├── CLAUDE.md                # этот файл — схема
├── index.md                 # каталог всех страниц с однострочниками
├── log.md                   # append-only хронология действий LLM
├── raw/                     # неизменяемые исходники
│
├── entities/                # сущности проекта (модули, роли, артефакты)
│   ├── inbox-wiki.md
│   ├── classifier.md
│   ├── job-model.md
│   └── ...
├── concepts/                # концепции и паттерны
│   ├── two-stage-launch.md
│   ├── anti-nesting.md
│   └── ...
├── decisions/               # черновики решений (станут ADR при переносе)
│   ├── D-001-job-model.md
│   ├── D-002-scheduler-backend.md
│   └── ...
├── questions/               # разбор open questions из overview §9 (Tier A/B/C/D/E)
│   ├── Q-A-01-job-table.md
│   ├── Q-A-02-scheduler.md
│   └── ...
└── research/                # внешние материалы, прочитанное, сравнения
    ├── apscheduler-vs-cron.md
    ├── karpathy-wiki-notes.md
    └── ...
```

## 4. Конвенции именования

1. **Файлы:** `kebab-case.md`. Без пробелов, без кириллицы в именах файлов.
2. **Заголовки внутри:** свободно, на русском.
3. **Префиксы для упорядочивания:**
   1. `decisions/D-NNN-<slug>.md` — черновики решений (NNN = 001, 002, …).
   2. `questions/Q-<TIER>-NN-<slug>.md` — разбор open questions (`Q-A-01`, `Q-B-09`).
   3. `entities/`, `concepts/`, `research/` — без префиксов.
4. **Бэклинки:** Markdown-линки на относительные пути: `[Inbox-WIKI](../entities/inbox-wiki.md)`.
5. **Цитаты из `raw/`:** обязательно ссылка вида `см. raw/<файл>` + блок-цитата.
6. **Дата в логе:** `## [YYYY-MM-DD] <op> | <title>` — формат для парсинга. Применяется **только** к Spec-WIKI/log.md (design-time мета-зона). Runtime `<Domain>-WIKI/log.md` использует ISO 8601 с TZ-offset per [decisions/D-040](decisions/D-040-log-date-format.md). `<op> ∈ {init, ingest, query, lint, refactor, decision, wave}` — closed canonical set для новых записей. Исторические записи до schema-bump 2026-05-09 могут содержать legacy `wave-N`; их не редактировать из-за append-only правила. Семантика op:
   1. `init` — создание самой вики или раздела.
   2. `ingest` — внешний материал из `raw/` обработан в страницы.
   3. `query` — ответ на вопрос пользователя сохранён обратно как страница.
   4. `lint` — аудит без изменений содержимого (read-only).
   5. `refactor` — реструктуризация существующих страниц (rename, merge, split, переписывание секций) ИЛИ изменение самой конституции (`CLAUDE.md`).
   6. `decision` — артефакт-решение `D-NNN` принят, обновлён или помечен superseded.
   7. `wave` — открытие или закрытие тематического батча решений (агрегатор-маркер).
   Каждая op описывает **что физически произошло с вики**, а не upstream-причину. `decision` и `wave` не сводятся к `refactor`: артефакт-решение само по себе и тематический батч — самостоятельные события графа знаний.

## 5. Шаблоны страниц

### entity / concept

```markdown
# <Название>

**Тип:** entity | concept
**Статус:** draft | review | stable | obsolete
**Источники:** [raw/foo.pdf](raw/foo.pdf), [overview §3a](../20260507-ai-steward-wiki-only-overview.md)

## Суть
Одно-два предложения.

## Детали
...

## Связанные
- [<page>](path)

## Открытые вопросы
1. ...
```

### decision (D-NNN)

```markdown
# D-NNN: <название решения>

**Статус:** proposed | accepted | superseded-by D-MMM
**Дата:** YYYY-MM-DD
**Контекст:** ссылка на question/entity

## Проблема
## Варианты
1. **A:** ... — плюсы / минусы
2. **B:** ... — плюсы / минусы
## Выбор
## Последствия
## Перенос в ADR
- [ ] перенесено в `docs/adr/ADR-NNN-...md` (когда финализируется)
```

### question (Q-TIER-NN)

```markdown
# Q-TIER-NN: <вопрос из overview §9>

**Tier:** A | B | C | D | E
**Источник:** overview §9 п.NN

## Формулировка
## Варианты ответа
## Решение
- [ ] оформлено как `decisions/D-NNN-...md`
```

## 6. Правила работы для Claude

1. **Перед изменением страницы** — прочитать связанные через бэклинки.
2. **Каждое изменение** → запись в `log.md` (append-only).
3. **Каждая новая страница** → запись в `index.md` (соответствующая категория).
4. **Конфликты с существующими страницами** — не молча перезаписывать, а пометить статус `review` и завести запись в `log.md` с типом `lint`.
5. **Граница:** не редактировать ничего за пределами `Spec-WIKI/` без явного запроса. Артефакты в `docs/superpowers/`, `docs/adr/` правит юзер при «переносе».
6. **Без кода:** в `Spec-WIKI/` не лежит исполняемый код. Только Markdown + бинарные исходники в `raw/`.
7. **Без feature-workflow:** не запускать `bd create`, не требовать USER APPROVAL gates, не создавать `discovery.md` / `design.md` / `plan.md` здесь — это делается при переносе наружу.
8. **Aggregator-invariants:** любая сводная страница, которая агрегирует ≥3 `decisions/D-NNN-*.md` (например `research/tech-spec-draft.md`), ОБЯЗАНА содержать в начале блок `## 0. Edit invariants` с closed checklist'ом grep-проверяемых правил (coverage против каждого D-файла, identity vocabulary, числовые ссылки на SSoT, запрещённые конструкции). Перед каждым commit'ом, меняющим такую страницу, редактор прогоняет verification ritual из её §0. **Почему:** git-история 2026-05-09…2026-05-10 показала 3 волны фиксов одного и того же drift'а tech-spec ↔ D-файлы; checklist в шапке превращает молчаливое предположение «я помню SSoT» в воспроизводимый шаг. При появлении нового D-файла checklist обновляется в том же commit'е, что и сама страница.

## 7. Связь с overview

Главный внешний источник — `raw/20260507-ai-steward-wiki-only-overview.md`. При ingest нового материала:

1. Сравнить с overview §1–§11.
2. Если материал отвечает на open question из §9 — создать/обновить `questions/Q-<TIER>-NN-...md`.
3. Если материал предлагает архитектурное решение — `decisions/D-NNN-...md`.
4. Если описывает сущность/концепт — `entities/` или `concepts/`.

## 8. Lifecycle: research → перенос

1. Страница начинается со статусом `draft`.
2. После обсуждения с юзером — `review`.
3. После апрува юзером — `stable`.
4. **При переносе** в `docs/superpowers/specs/` или `docs/adr/` — статус становится `obsolete`, в начале страницы добавляется ссылка `→ перенесено в <путь>`. Удалять не надо — это history.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
