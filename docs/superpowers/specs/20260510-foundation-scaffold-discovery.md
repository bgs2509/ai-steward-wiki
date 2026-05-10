---
title: "Foundation scaffold — Discovery"
feature: foundation-scaffold
bd_id: aisw-u99
epic_bd_id: aisw-fm0
date: 2026-05-10
status: draft
sources:
  - docs/Spec-WIKI/research/tech-spec-draft.md#10.3
  - CLAUDE.md (project stack)
  - ~/.claude/CLAUDE.md (Pre-commit Policy, Plan Sizing)
fr:
  - id: FR-1
    title: "Project bootstrappable via `uv sync`"
    desc: "pyproject.toml declares Python 3.11+ and pinned deps; `uv sync` produces uv.lock and a working venv."
  - id: FR-2
    title: "Settings load from .env via pydantic-settings"
    desc: "src/ai_steward_wiki/settings.py exposes Settings BaseSettings with frozen fields populated from .env / env vars."
  - id: FR-3
    title: "Structlog JSON-lines logger ready"
    desc: "logging_setup.configure_logging() outputs structlog JSON to stdout (journald-friendly) with mandatory fields ts, event, correlation_id, user_id, wiki_id, job_id."
  - id: FR-4
    title: "correlation_id contextvar"
    desc: "Public helper bind_correlation_id(value) sets a contextvar bound to every log line emitted within the async task."
  - id: FR-5
    title: "Lint/QA targets"
    desc: "Makefile exposes `make lint` (ruff check + ruff format --check + mypy --strict src/) and `make qa` (lint + pytest)."
  - id: FR-6
    title: "Pre-commit config"
    desc: ".pre-commit-config.yaml runs ruff (check + format), mypy on staged src/, gitleaks. Hook installed via `pre-commit install`."
nfr:
  - id: NFR-1
    title: "mypy --strict clean for src/"
  - id: NFR-2
    title: "ruff lint+format clean"
  - id: NFR-3
    title: "Logger overhead negligible for hot-path (no per-line dict-merge cost beyond structlog defaults)"
  - id: NFR-4
    title: "No secrets in repo — gitleaks pre-commit + .env gitignored"
constraints:
  - "Python 3.11+, uv only (no pip-tools, no Poetry)"
  - "Versions strictly pinned with `==` per ~/.claude/rules/python-dev.md"
  - "All code/comments/commits in English; user-facing strings in Russian (later chunks)"
  - "structlog JSON output, not human-readable; rendering belongs to journald/Loki"
risks:
  - id: R-1
    desc: "Drift between pyproject pinned versions and uv.lock"
    mitigation: "uv.lock checked into git; CI/pre-commit verify lock consistency"
  - id: R-2
    desc: "Bd-installed pre-commit hook conflicts with project hook"
    mitigation: "Use the `pre-commit` framework so hook installation is idempotent; bd's existing hook is preserved by bd itself"
scope_in:
  - pyproject.toml + uv.lock
  - .env.example with full settings list (skeleton — fields refined per chunk)
  - .pre-commit-config.yaml (ruff, mypy on staged, gitleaks)
  - Makefile (lint, qa, test, format targets)
  - src/ai_steward_wiki/__init__.py (version export)
  - src/ai_steward_wiki/settings.py (pydantic-settings BaseSettings)
  - src/ai_steward_wiki/logging_setup.py (structlog config + correlation_id contextvar)
  - tests/unit/test_settings.py (smoke)
  - tests/unit/test_logging_setup.py (correlation_id propagation)
  - .gitignore (extend for venv, .env, uv cache, .sentrux/{baselines,results})
scope_out:
  - DB engines / Alembic — Chunk 2
  - Any service modules (storage, scheduler, classifier, tg, …) — later chunks
  - Real systemd unit files — Chunk 16
  - CI workflow file (Makefile-only for MVP per spec §10.6)
deps:
  - aiogram==3.15 (deferred install; declare in pyproject for stability lock, used Chunk 10)
  - APScheduler (Chunk 4) — declare for lock
  - SQLAlchemy 2.x async + aiosqlite (Chunk 2)
  - alembic (Chunk 2)
  - pydantic v2, pydantic-settings (this chunk)
  - structlog (this chunk)
  - faster-whisper (Chunk 11) — heavy; declare optional extra `[stt]`
  - dateparser (Chunk 5) — declare
verification:
  - "uv sync exits 0; uv.lock present"
  - "uv run python -c 'from ai_steward_wiki import __version__; print(__version__)' works"
  - "make lint passes (ruff + ruff format --check + mypy --strict src/)"
  - "uv run pytest tests/unit -q passes"
  - "bind_correlation_id('xyz') propagates through async tasks (asserted in test)"
  - "pre-commit run --all-files exits 0 (after install)"
---

# Foundation scaffold — Discovery

Cборка минимального dev-окружения, без которого нельзя начать chunk 2+. Stack полностью предопределён в `tech-spec-draft.md` и `CLAUDE.md`, поэтому Discovery компактный.

## Intent

Превратить пустой репозиторий (`docs/` only) в Python-пакет, который проходит lint+test+pre-commit, имеет настройки через `.env` и базовую структуру логирования. Дальнейшие чанки добавляют код в `src/ai_steward_wiki/<subpackage>/`.

## Что НЕ в этом чанке

База, scheduler, classifier, tg, ops — всё это последующие чанки. Здесь только «фундамент»: deps + settings + logging + lint + pre-commit + Makefile.

## Pre-flight результат

- `.git/hooks/pre-commit` (1KB) уже стоит от bd — оставляем нетронутым.
- `.pre-commit-config.yaml` отсутствует — добавим в этом чанке.
- Sentrux не онбордится в этом репо (`.sentrux/rules.toml` отсутствует) — пропускаем silently.
- Lint baseline: 0 (нет `*.py`).

## Open Questions

Нет — стек однозначен. Все цифры (Python 3.11+, mypy --strict, ruff) — прямые цитаты из spec/CLAUDE.md.
