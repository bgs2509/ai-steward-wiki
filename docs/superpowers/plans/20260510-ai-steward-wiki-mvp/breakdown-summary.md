# Breakdown — ai-steward-wiki MVP

**Epic slug:** `20260510-ai-steward-wiki-mvp`
**Target repo:** `/home/bgs/ai-steward/Gena_Beeline_Local/ai-steward-wiki`
**Draft SSoT:** `docs/Spec-WIKI/research/tech-spec-draft.md` (42 D-решения, 14 INV)
**Chunks:** 17
**Budget:** 0.6 × Opus 4.7 effective window per chunk
**Mandatory gate:** this approval (Phase 4); per-chunk feature-workflow gates остаются в силе

## Stack (фиксируется в Chunk 1)

Python 3.11+ / uv / aiogram 3.x / APScheduler AsyncIOScheduler+SQLAlchemyJobStore / 3×SQLite (WAL+busy_timeout+foreign_keys) / Alembic per-DB / Pydantic v2 / Claude Code CLI (subscription) / faster-whisper / dateparser / structlog / systemd.

## Зависимости

```
1 ─┬─ 2 ─┬─ 3 ─┬─ 6 ─┐
   │     │     │     │
   │     ├─ 4 ─┤     │
   │     │     │     ├─ 7 ─┬─ 8 ─ 15
   │     ├─ 5 ─┘     │     │
   │     │           │     ├─ 14 ─┐
   │     ├─ 9 ───────┤     │      ├─ 16 ─ 17
   │     │           │     │      │
   │     └─ 13 ──────┘     │      │
   │                       │      │
   └────────── 10 ─ 11 ─ 12 ──────┘
```

(Chunk 17 — финальный, depends_on=all.)

## Chunks

| # | bd_id | Title | Window | Depends |
|---|-------|-------|--------|---------|
| 1 | aisw-1 | Foundation scaffold (pyproject, settings, structlog, pre-commit) | 0.30 | — |
| 2 | aisw-2 | Storage: 3 SQLite + Alembic + Pydantic discriminated union | 0.55 | 1 |
| 3 | aisw-3 | Identity & users.toml allowlist (SIGHUP + watchdog) | 0.40 | 2 |
| 4 | aisw-4 | Scheduler core: queue, semaphore, locks, DLQ, taxonomy | 0.60 | 2 |
| 5 | aisw-5 | Classifier Stage-0 Haiku + NL time parser | 0.50 | 1, 2 |
| 6 | aisw-6 | Inbox-WIKI materialize + hint cache | 0.45 | 2, 3 |
| 7 | aisw-7 | Wiki Stage-1a/1b runner (Sonnet CLI + streaming + locks) | 0.55 | 4, 5, 6 |
| 8 | aisw-8 | Wiki lifecycle (NL pre-flight, anti-spam, soft-delete, frontmatter) | 0.55 | 7 |
| 9 | aisw-9 | Idempotency (tg_updates 24h + seen_files 30d) | 0.25 | 2 |
| 10 | aisw-10 | TG text I/O (allowlist, confirmations, sizing, streaming) | 0.60 | 3, 7, 9 |
| 11 | aisw-11 | Voice & photo (faster-whisper + vision + staging→raw) | 0.40 | 10 |
| 12 | aisw-12 | Onboarding + admin (intro lint, /admin elevate, TENANCY_MODE) | 0.45 | 3, 10 |
| 13 | aisw-13 | PII redactor + retention purge jobs | 0.45 | 2, 4 |
| 14 | aisw-14 | Backup MVP + per-WIKI git + restore-test | 0.35 | 2, 7 |
| 15 | aisw-15 | Templates (10 doменов + _default + inbox + onboarding) + migrations | 0.30 | 8 |
| 16 | aisw-16 | Deployment (systemd units, slices, sysusers, runbook) | 0.40 | 1, 13, 14 |
| 17 | aisw-17 | Verification: 14 INV grep-lints + integration nightly + E2E checklist | 0.30 | all |

## Auto-applied decisions (3x-rule)

Все технологические выборы уже зафиксированы в 41 active D-файлах spec'а — переутверждение не требуется. Chunk 1 только консолидирует их в `pyproject.toml` + `docs/technology.xml`.

## Halt-and-ask candidates

Нет на этапе breakdown. Возможные точки остановки во время execution: первый интеграционный запуск Claude CLI (Chunk 5/7) — сверим JSON-schema контракт; формат systemd-credentials для optional API backend (Chunk 5); конкретный gitleaks ruleset (Chunk 14).

## Out of scope (per spec §10.2 / §11)

Off-site backup (3-2-1, borg/restic), git remote push, at-rest crypto, multi-tenancy реальная (только флаг готов), i18n catalog, deployment automation (CI/CD pipeline сверх Makefile). Stop at "merged on local branch".

## Verification ritual после каждого chunk

`uv run ruff check && ruff format --check && mypy && grace lint && pytest && (если bulk-edit) make lint full`. Bypass запрещён.
