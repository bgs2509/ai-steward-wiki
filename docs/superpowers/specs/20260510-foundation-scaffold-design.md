---
title: "Foundation scaffold — Design"
feature: foundation-scaffold
bd_id: aisw-u99
date: 2026-05-10
status: draft
stack:
  python: "3.11+"
  package_manager: uv
  lint:
    - ruff (check + format)
    - mypy --strict (src/ only)
    - gitleaks
  config: pydantic-settings v2
  logging: structlog (JSON renderer → stdout)
  test: pytest + pytest-asyncio (anyio mode auto)
deps_pinned:
  runtime:
    - "pydantic==2.9.2"
    - "pydantic-settings==2.6.1"
    - "structlog==24.4.0"
  deferred_runtime:
    - "aiogram==3.15.0"
    - "APScheduler==3.11.0"
    - "SQLAlchemy==2.0.36"
    - "aiosqlite==0.20.0"
    - "alembic==1.14.0"
    - "dateparser==1.2.0"
  optional_extras:
    stt:
      - "faster-whisper==1.1.0"
  dev:
    - "pytest==8.3.4"
    - "pytest-asyncio==0.24.0"
    - "ruff==0.8.4"
    - "mypy==1.13.0"
    - "pre-commit==4.0.1"
adrs: []
---

# Foundation scaffold — Design

Стек жёстко зафиксирован в `tech-spec-draft.md` и `CLAUDE.md`; brainstorming сводится к фиксации точных версий пинов и мини-API двух модулей.

## Module map (этот чанк)

```
src/ai_steward_wiki/
├── __init__.py            # __version__ = "0.0.1"
├── settings.py            # Settings(BaseSettings) — frozen, env_file=".env", env_prefix="AISW_"
└── logging_setup.py       # configure_logging(level), bind_correlation_id(value), get_logger(name)
```

## settings.py — sketch

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AISW_", frozen=True)
    log_level: Literal["DEBUG","INFO","WARNING","ERROR"] = "INFO"
    workspace_root: Path
    claude_config_dir: Path = Path("/var/lib/ai-steward-wiki/claude-code")
    # фактические поля будут пополняться по чанкам — это первичный skeleton
```

## logging_setup.py — sketch

```python
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

def bind_correlation_id(value: str) -> Token[str | None]: ...

def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            _inject_correlation_id,    # читает ContextVar
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(...),
    )
```

`_inject_correlation_id` берёт из contextvar и кладёт в event_dict — гарантирует поле в каждой записи даже когда callsite его явно не передал. Поля `user_id, wiki_id, job_id` подбрасываются через `structlog.contextvars.bind_contextvars(...)` на boundary'ях (TG handler, scheduler firing) в последующих чанках; в этом чанке только инфраструктура.

## Makefile — targets

```
lint:    ruff check . && ruff format --check . && mypy src/
format:  ruff format . && ruff check --fix .
test:    uv run pytest tests/ -q
qa:      $(MAKE) lint && $(MAKE) test
```

## .pre-commit-config.yaml — hooks

1. `ruff` (check + format) — official pre-commit-ruff repo
2. `mypy` — local stage on staged `src/**.py`
3. `gitleaks` — official `gitleaks/gitleaks` pre-commit hook (per CLAUDE.md secrets policy)
4. End-of-file fixer + trailing-whitespace — pre-commit-hooks

bd's `.git/hooks/pre-commit` остаётся (`pre-commit install` использует `core.hooksPath` или `pre-commit-multi`). Если конфликт — `pre-commit install --hook-type pre-commit -f` с переносом bd-команд в `local` hook (решим в Execution при первом конфликте).

## .env.example — single template (AISW_ENV switch + dual TG tokens)

Корневой `.env.example` — единственный шаблон. Профиль выбирается через `AISW_ENV=local|vps`; локальный и продовый TG-токены живут в разных полях, активный выбирается по `env`. Для VPS файл инсталлируется как `/etc/ai-steward-wiki/.env` (`0640 root:aisw-bot`).

```
AISW_ENV=local
AISW_LOG_LEVEL=INFO
AISW_WORKSPACE_ROOT=/var/lib/ai-steward-wiki/workspace
AISW_CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code
AISW_TG_BOT_TOKEN_LOCAL=__SET_ME_LOCAL__
AISW_TG_BOT_TOKEN_PROD=__SET_ME_PROD__
AISW_TG_ADMIN_TELEGRAM_IDS=__SET_ME__
```

`Settings._check_tg_token_for_env` валидатор требует `tg_bot_token_prod` при `env='vps'`; при `env='local'` оба слота могут быть пусты (для unit-тестов без живого бота).

## Tests (этот чанк)

1. `tests/unit/test_settings.py` — Settings грузится из tmp `.env`, `frozen=True`, неверный `log_level` → `ValidationError`.
2. `tests/unit/test_logging_setup.py` — `bind_correlation_id("c1")` → следующий `logger.info("e")` содержит `correlation_id=c1`; внутри `asyncio.gather` две задачи имеют разные binding'и.

## Open trade-offs / decisions

Нет дизайн-альтернатив, требующих ADR — все выборы продиктованы spec'ом.
