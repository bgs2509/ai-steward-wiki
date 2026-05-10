---
project_type: dev
project_name: ai-steward-wiki
language: python
python_version: "3.11"
package_manager: uv
spec_source: docs/Spec-WIKI/research/tech-spec-draft.md
---

# ai-steward-wiki

> Изолированный мультипользовательский Telegram-сервис, который превращает Claude Code CLI в персонального WIKI-ассистента (метод Karpathy LLM Wiki). Отдельная VPS, никакой связи с `ai-steward` (TG-бот) по умолчанию.

## Назначение

1. Юзер общается с ботом естественным языком (текст / файл / фото / голос).
2. Без явного указания папок и команд.
3. Бот идентифицирует автора по `telegram_id`, классифицирует контент через `Stage-0 Haiku → Stage-1a/1b Sonnet`, запускает Claude в нужной `<Domain>-WIKI/`.

Полная спецификация — `docs/Spec-WIKI/research/tech-spec-draft.md` (42 D-решения, status=stable).

## Изоляция (Spec-WIKI/CLAUDE.md §1.1)

Никаких пересечений, миграций, импортов формата, cross-service чтений с `ai-steward` (`/home/bgs/ai-steward/`). Default — нулевая связь.

## Стек

1. **Python 3.11+**, `uv` package manager.
2. **aiogram 3.x** — Telegram Bot API (asyncio).
3. **APScheduler** `AsyncIOScheduler` + `SQLAlchemyJobStore` — scheduling, in-process.
4. **3 × SQLite** (`jobs.db`, `audit.db`, `sessions.db`) с разными bounded contexts; **Alembic per-DB**; WAL + busy_timeout + foreign_keys.
5. **Pydantic v2** — discriminated union для job-payload validation.
6. **Claude Code CLI** (subscription auth, `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`) — Stage-0 Haiku, Stage-1a/1b Sonnet.
7. **faster-whisper** (CTranslate2, CPU) — voice STT.
8. **dateparser** + Haiku-fallback — NL time parsing.
9. **structlog** JSON-lines → journald.
10. **systemd** + `systemd-run --scope` per CLI invocation, dedicated `aisw-*` UIDs, slices.

## Структура

```
ai-steward-wiki/
├── CLAUDE.md                       # этот файл
├── pyproject.toml
├── uv.lock
├── .pre-commit-config.yaml
├── .env.example
├── src/ai_steward_wiki/
│   ├── storage/{jobs,audit,sessions}/
│   ├── scheduler/
│   ├── classifier/
│   ├── inbox/
│   ├── wiki/
│   ├── tg/
│   ├── auth/
│   └── ops/
├── tests/{unit,integration,e2e}/
├── alembic/{jobs,audit,sessions}/
├── prompts/                        # classifier.md, wiki.md, inbox.md, domain-*.md
├── templates/                      # health.md, health-lite.md, …, _default.md
├── deploy/{systemd,staging,prod}/
├── scripts/
└── docs/
    ├── Spec-WIKI/                  # research / design SSoT (life-зона, отдельный CLAUDE.md там)
    ├── superpowers/{specs,plans}/
    ├── adr/
    ├── reports/
    ├── runbook/
    ├── requirements.xml            # auto-generated
    ├── technology.xml              # auto-generated
    ├── development-plan.xml        # aggregated
    ├── verification-plan.xml       # derived
    └── knowledge-graph.xml         # derived (MODULE_CONTRACT)
```

## Запуск

```bash
uv sync                                          # install deps
uv run alembic -c alembic/jobs/alembic.ini upgrade head    # init DBs (повторить per-DB)
uv run alembic -c alembic/audit/alembic.ini upgrade head
uv run alembic -c alembic/sessions/alembic.ini upgrade head
uv run python -m ai_steward_wiki                 # bot entrypoint
```

## Тестирование

```bash
uv run pytest tests/unit                         # unit, ≥80% core coverage
RUN_INTEGRATION=1 uv run pytest tests/integration   # nightly, реальный Claude CLI
make lint                                        # ruff + ruff format + mypy + grace lint
make qa                                          # lint + unit + integration
```

## Деплой

1. Код: `/opt/ai-steward-wiki/` (VPS).
2. Auth: `/var/lib/ai-steward-wiki/claude-code/` (read-only для CLI scopes).
3. Systemd: `aisw-bot.service` + `aisw-bot.slice`, per-CLI transient `cli-<job_id>.scope` через `systemd-run`.
4. Логи: stdout → journald (structlog JSON).
5. Backup MVP: ежедневный `db_snapshot` (`VACUUM INTO`) + per-WIKI git history. Off-site — deferred.

## Соглашения

1. **Type hints** обязательны (`mypy --strict` для `src/`).
2. **Pydantic** на всех boundaries (TG inputs, CLI outputs, DB schemas).
3. **structlog** с обязательными полями: `ts, event, correlation_id, user_id, wiki_id, job_id`.
4. **Conventional Commits** + GRACE MODULE_ID scope: `feat(M-CLASSIFIER-STAGE0): ...`.
5. **TDD:** RED → GREEN → REFACTOR. Никакого production-кода без failing теста.
6. **Все datetime в БД — UTC.** User-TZ применяется только на input/output.
7. **Identity vocabulary** (D-042): `telegram_id` (canonical), `owner_telegram_id` (jobs owner), `chat_id` (delivery), `user_id` (internal DB surrogate). Не путать.
8. **Никаких bypass'ов** хуков (`--no-verify`, `SKIP=`) — fail = fix root cause.
9. **Никакого автоматического `git push`** кода — только по явному запросу юзера.
10. **Ru-only** в MVP (D-032). Все user-facing строки на русском, без i18n catalog.

## Workflow

Этот репозиторий — `dev`-проект. Применяются:

1. **GRACE** — MODULE_CONTRACT, knowledge-graph, verification-plan.
2. **`feature-workflow`** — единственная точка входа для новой фичи / bugfix / значимого изменения.
3. **3 mandatory USER APPROVAL gates** в `feature-workflow` (после Discovery, после Brainstorming, после Writing Plans).
4. **Beads** — task tracking, `bd_id` как universal cross-reference.
5. **`/superautocoder`** — для разбиения большого draft'а (типа `tech-spec-draft.md`) на цепочку chunked feature-workflow итераций.

Глобальные правила — `~/.claude/CLAUDE.md`. Этот файл не дублирует их, только project-specific.

## Граница с `docs/Spec-WIKI/`

`docs/Spec-WIKI/` имеет **локальный override** в виде собственного `CLAUDE.md`: это life-зона (Karpathy LLM Wiki) для research / design / decisions. Внутри — только Markdown, никакого исполняемого кода. На остальной репозиторий это правило не распространяется.
