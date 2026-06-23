---
bd_id: aisw-0a5
feature: migrate-ai-steward-userdata
title: Миграция user-data ai-steward (vpn-0) → ai-steward-wiki (vpn-gpu-1)
date: 2026-05-13
status: approved
risk: high
risk_justification: DB writes (jobs.db truncate+reload), cross-machine cutover, auth surface (users.toml creation), irreversible после announce юзерам.
evidence: strong
evidence_justification: 17 архитектурных решений зафиксированы через /questions-answers (источники — ground-truth from src/, prescan от старых planner.json, D-005/D-007/D-008/D-041/D-042 проектные decisions, Karpathy LLM Wiki gist, AWS DMS / Stripe migration best practices).
open_questions: []
fr:
  - id: FR-01
    text: Создать users.toml с 5 entries (763463467 admin, 6156629438 user, 1151678530 user, 1122408606 user, 8587590035 user); display_name, tz=Europe/Moscow, lang=ru per user.
  - id: FR-02
    text: Скопировать {User}/{Project}/CLAUDE.md → <Domain>-WIKI/raw/legacy-ai-steward-claude-md.md с frontmatter (source path, snapshot date) для каждого mapping-pair.
  - id: FR-03
    text: ETL planner.json → jobs.db — active items (status=pending AND (date≥today OR repeat≠null)) разворачиваются в Job rows с fan-out по remind_before.
  - id: FR-04
    text: ETL planner.json history (completed/skipped/cancelled/expired) → <Domain>-WIKI/raw/legacy-planner-history.md (Markdown summary per source planner).
  - id: FR-05
    text: planner.json repeat=monthly → CronUserPayload.cron_expr="M H D * *" (5-field) + payload.legacy_source="planner.json:monthly".
  - id: FR-06
    text: planner.json category mapping — medication→medication, event→event, остальное→generic; payload.legacy_category хранит original value.
  - id: FR-07
    text: "planner.json recipients → Job.chat_id = recipients[0] (prescan: 0 multi). При встрече len(recipients)>1 — fail-fast."
  - id: FR-08
    text: Скопировать data/ структуру — CSV/JSON → <Domain>-WIKI/<subfolder>/<file>, PDF/JPG → <Domain>-WIKI/raw/<subfolder>/<file>. Скрипты (.py) — drop.
  - id: FR-09
    text: Скопировать _output/* → <Domain>-WIKI/raw/legacy-output/<file>. PDF, MD, CSV, TXT — все туда; скрипты drop.
  - id: FR-10
    text: Скопировать root-level PDF/JPG (не CLAUDE.md/planner.json/data/_output) → <Domain>-WIKI/raw/legacy-root/<file>.
  - id: FR-11
    text: Создать новые WIKI-папки для несуществующих доменов (Career-WIKI, Budget-WIKI, Default-WIKI, Weightwatch-WIKI) — из соответствующих templates/<name>.md или _default.md.
  - id: FR-12
    text: WIKI namespacing — каждый WIKI принадлежит одному owner_telegram_id; 6156629438 имеет собственный Career-WIKI, отдельный от 763463467 Career-WIKI.
  - id: FR-13
    text: Создать пустую папку /home/bgs/.local/share/ai-steward-wiki/data/profiles/ и env var AISW_PROFILES_DIR (для будущего onboarding). profiles/<id>.md не генерируются.
  - id: FR-14
    text: ETL имеет --dry-run режим, генерирующий migration_report.md без записи в БД/файлы. Полный отчёт о том, что будет сделано (counts, mappings, dropped items, edge-cases).
  - id: FR-15
    text: Реальный run работает Fail-Fast — exception на любую неожиданность блокирует ETL. Snapshot БД до запуска сохраняется в /tmp.
  - id: FR-16
    text: "Cutover: остановка ai-steward bot на vpn-0 (systemctl stop или pkill) → rsync /home/bgs/ai-steward/ → vpn-gpu-1:/tmp/migration-snapshot/ → dry-run → ETL real → restart ai-steward-wiki bot."
  - id: FR-17
    text: Старый бот после cutover stopped permanently; /home/bgs/ai-steward/ на vpn-0 остаётся read-only cold archive.
nfr:
  - id: NFR-01
    text: Idempotency — повторный запуск ETL после truncate jobs.db даёт идентичный результат (deterministic ordering, stable autoincrement через ORDER BY на читаемых planner items).
  - id: NFR-02
    text: Observability — каждый Job row имеет payload.legacy_item_id ссылающийся на исходный UUID; migration_report.md содержит per-user/per-project counts; structlog JSON logs для каждого ETL phase.
  - id: NFR-03
    text: Safety — pre-ETL snapshot БД (sqlite3 .backup), rollback возможен через restore snapshot + clear filesystem changes.
  - id: NFR-04
    text: Maintenance window ≤ 30 минут (от stop старого бота до restart нового).
  - id: NFR-05
    text: Coverage — unit tests для core mapping функций (planner→Job, fan-out, category-mapping, monthly cron) ≥80%.
constraints:
  - Старого ai-steward код / конфиги — не трогаем; только read snapshot.
  - Новый ai-steward-wiki код — не правим в migration scope (тулинг = новый модуль/скрипт + новая env var; runtime код existing не меняется).
  - users.toml schema v1 не bumpaem; используем существующие поля (telegram_id, role, display_name, tz, lang).
  - Recurrence (Pydantic) НЕ расширяем под monthly — workaround через cron_user.cron_expr; typed расширение — follow-up beads, не блокер.
  - Никакого автоматического git push (правило ~/.claude/CLAUDE.md).
risks:
  - id: R-01
    impact: high
    text: ETL пишет в production jobs.db до cutover — может сломать running бот.
    mitigation: ETL запускается ТОЛЬКО после stop бота на vpn-gpu-1 (kill PID 962854); pre-ETL snapshot jobs.db.
  - id: R-02
    impact: medium
    text: Старый бот на vpn-0 продолжает писать в planner.json во время rsync.
    mitigation: systemctl stop ai-steward на vpn-0 ДО rsync. Бот ai-steward — systemd unit; sudo доступ требуется.
  - id: R-03
    impact: medium
    text: Cron expression "M H D * *" может быть невалидным для CronUserPayload.cron_expr (нет validator-проверки в Pydantic — string).
    mitigation: ETL валидирует cron через APScheduler CronTrigger.from_crontab() до записи в payload.
  - id: R-04
    impact: medium
    text: WIKI namespacing per owner_telegram_id неясен — нет explicit code-confirmation, что Medical-WIKI под 763463467 ≠ Medical-WIKI под 1151678530.
    mitigation: Brainstorming phase Step 4 — изучить src/ai_steward_wiki/wiki/{name,lifecycle,acquire}.py для verification; если single global namespace — wiki names получают per-user suffix или префикс.
  - id: R-05
    impact: low
    text: WIKI templates требуют specific init-flow (CLAUDE.md, log.md, index.md). Простой mkdir + копирование template insufficient.
    mitigation: Использовать существующий lifecycle.py:acquire_wiki / wiki/init logic, не дублировать.
  - id: R-06
    impact: low
    text: Старые planner items с invalid TZ/format могут сломать TZ-конверсию MSK→UTC.
    mitigation: dry-run выявит, ETL Fail-Fast при первом разрыве.
  - id: R-07
    impact: medium
    text: SQLite jobs.db в WAL mode, бот удерживает lock — ETL не сможет писать.
    mitigation: ETL writeс выполняется при остановленном боте; перед ETL — sqlite3 PRAGMA wal_checkpoint(TRUNCATE).
scope:
  in:
    - ETL-tooling: "`scripts/migrate_legacy.py` (or `src/ai_steward_wiki/migration/`)"
    - users.toml generation
    - planner.json → jobs.db transformation (Job + JobPayload discriminated union)
    - File copy operations per FR-08/09/10
    - <Domain>-WIKI bootstrap для несуществующих
    - migration_report.md generation
    - Unit tests для core mapping
    - ".env.example update: AISW_PROFILES_DIR"
  out:
    - Изменения в runtime коде ai-steward-wiki (classifier, scheduler, tg-handlers, wiki, auth)
    - Typed Recurrence monthly (D-NNN) — follow-up beads issue
    - systemd unit для нового бота — follow-up
    - AISW_ENV=local→vps switch — follow-up
    - Profile prose extraction (Q6/V4) — re-onboarding юзеров заполнит
    - Cross-machine sync, live replication, dual-write — V1 cutover only
  later:
    - Recurrence.kind=monthly typed
    - profile prose ingest когда юзер заполнит
dependencies:
  affects:
    - data/users.toml — создаётся
    - data/jobs.db — truncate+reload (но preserves schema!)
    - /home/bgs/.local/share/ai-steward-wiki/workspace/wikis/ — populated
    - /home/bgs/.local/share/ai-steward-wiki/data/profiles/ — empty dir created
    - .env / .env.example — adds AISW_PROFILES_DIR
  affected_by:
    - existing alembic schema (jobs.db, audit.db, sessions.db) — must be migrated to head before ETL
    - existing wiki/lifecycle.py — used for WIKI init
    - users.toml schema v1 — used as-is
references:
  - D-005: no planner.json (старый формат disallowed; SSoT — jobs.db)
  - D-007: --add-dir scope только <wiki>
  - D-008: WIKI marker regex ^[A-Z][A-Za-z0-9]*-WIKI$
  - D-041: append-data CSV/JSON в WIKI root (не raw/)
  - D-042: telegram_id canonical identity vocabulary
  - Karpathy LLM Wiki: 3-layer (raw/, wiki pages, CLAUDE.md schema)
  - AWS DMS migration best practices: snapshot + ETL + cutover для different-schema
---

# Discovery: Миграция user-data ai-steward → ai-steward-wiki

## Реальная цель

Перенести существующие данные 5 пользователей (Геннадий + 2 TG-аккаунта, Татьяна, Дари, Марат) со старого Telegram-бота `ai-steward` (vpn-0) в новый изолированный сервис `ai-steward-wiki` (vpn-gpu-1, уже задеплоен и работает с пустым allowlist), при этом:

1. Конвертировать данные между принципиально разными схемами хранения (flat-file `{User}/{Project}/` ↔ SQLite + per-WIKI markdown)
2. Сохранить максимум user-visible поведения (активные напоминания работают; история доступна как Karpathy raw-source)
3. Без cross-service связей в будущем (default — нулевая интеграция между сервисами)

## Что юзер прямо не сказал, но критично

1. **Identity-mapping для всех 5 юзеров известен** (из `Gena_Beeline_Local/claude_bot/data/users.json`), включая факт, что Gena_MTS — **отдельный** telegram-аккаунт того же человека (6156629438 ≠ 763463467)
2. **WIKI templates новой системы advisory, не enforced** — runtime код не парсит «правильную» layout; Claude CLI читает `--add-dir` как обычные файлы
3. **D-041 (append-data) допускает CSV/JSON в WIKI root** — это сознательное project-level отступление от pure Karpathy для domain WIKI
4. **`Spec-WIKI` (research zone) ≠ Domain WIKI** — pure Karpathy применяется к Spec-WIKI; domain WIKI используют hybrid layout

## Blind spots (озвучены пользователю и закрыты решениями)

1. Gena_MTS — отдельный TG-аккаунт → требует второй entry в users.toml (Variant B)
2. planner.json `repeat=monthly` (4 items в Expenses) — нет typed support в новой Recurrence → workaround через cron_user.cron_expr
3. `_output/` содержит реальную мед-историю Тани с 2019 (не просто сгенерированные отчёты) → раскладка в `raw/legacy-output/`, LLM органически промоутит
4. Career-документы лежат внутри «dev-папок» (2025_Noveo, NOVEO-ORANGE) → content-by-nature classification, не по dir-name
5. WIKI namespacing per owner_telegram_id — **R-04** требует verification в Brainstorming

## FR / NFR

См. frontmatter `fr:` и `nfr:` (canonical SSoT). Здесь — рендер для человека.

### FR (17 функциональных)

1. **FR-01 users.toml** — 5 entries
2. **FR-02 CLAUDE.md → raw** — legacy-ai-steward-claude-md.md
3. **FR-03 planner active → jobs.db** — fan-out по remind_before
4. **FR-04 planner history → raw** — legacy-planner-history.md
5. **FR-05 monthly → cron_user** — cron_expr workaround
6. **FR-06 category mapping** — medication/event/generic + legacy_category
7. **FR-07 recipients[0]** — fail-fast на multi
8. **FR-08 data/ copy** — schema-aware (CSV/JSON in subfolders, PDF in raw)
9. **FR-09 _output → raw** — legacy-output/
10. **FR-10 root binary → raw** — legacy-root/
11. **FR-11 new WIKIs** — Career/Budget/Default/Weightwatch from templates
12. **FR-12 per-user namespace** — owner_telegram_id ownership
13. **FR-13 profiles dir + env var** — empty for now
14. **FR-14 --dry-run** — migration_report.md
15. **FR-15 Fail-Fast real run** — snapshot before, exception blocks
16. **FR-16 Cutover sequence** — stop → rsync → ETL → restart
17. **FR-17 archive old** — vpn-0 read-only

### NFR (5)

1. **NFR-01 Idempotency** через deterministic ordering + truncate+reload
2. **NFR-02 Observability** — legacy_item_id, migration_report, structlog
3. **NFR-03 Safety** — pre-ETL snapshot, rollback path
4. **NFR-04 Window ≤ 30 мин**
5. **NFR-05 Coverage ≥80%** core mapping

## Risks

См. frontmatter `risks:`. Самые горячие:
1. **R-01 (high)** — ETL пишет в production jobs.db до cutover. Mitigation: ETL только после stop бота.
2. **R-04 (medium)** — WIKI namespacing per-user неясен. Mitigation: проверить в Brainstorming Step 4 на коде.
3. **R-07 (medium)** — SQLite WAL lock при running боте. Mitigation: stop + wal_checkpoint.

## Scope

См. frontmatter `scope:`. Кратко:

**IN**: ETL tooling, users.toml gen, planner→jobs.db, file copy, WIKI bootstrap, dry-run, tests, env var.

**OUT**: правки runtime ai-steward-wiki, typed monthly Recurrence, systemd, prod-env switch, profile prose, live-sync.

**LATER**: typed monthly, profile prose enrichment через onboarding.

## Sources

1. **Project D-decisions** (`docs/Spec-WIKI/decisions/`): D-005, D-007, D-008, D-017, D-041, D-042 — file:line references confirm scope.
2. **Karpathy LLM Wiki** gist (https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — 3-layer architecture rationale.
3. **AWS DMS migration playbook** + **Stripe migration guide** — snapshot+ETL+cutover pattern для different-schema (Fowler "Patterns of Enterprise Application Architecture" Strangler-Fig).
4. **Ground-truth code**:
   - `src/ai_steward_wiki/auth/users_toml.py` — UserRecord schema
   - `src/ai_steward_wiki/storage/jobs/{models,payloads}.py` — Job, JobPayload discriminated union, Recurrence
   - `src/ai_steward_wiki/wiki/runner.py:210-224` — per-WIKI CLAUDE.md folding
   - `prompts/domain-finance.md:4` — raw/ convention for sensitive data
5. **Prescan data** (vpn-0 over SSH): 203 active planner items, 0 multi-recipient, 4 monthly repeats, category distribution.

## Open Questions

(пусто — все 17 решений зафиксированы, см. транскрипт сессии `/questions-answers`)
