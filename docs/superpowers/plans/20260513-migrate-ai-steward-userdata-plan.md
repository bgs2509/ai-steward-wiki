---
bd_id: aisw-0a5
feature: migrate-ai-steward-userdata
title: Implementation Plan — Migration ETL
date: 2026-05-13
status: approved
discovery_ref: 20260513-migrate-ai-steward-userdata-discovery.md
design_ref: 20260513-migrate-ai-steward-userdata-design.md
phases:
  - id: P1
    name: Skeleton + config (no logic yet)
    estimated_steps: 4
  - id: P2
    name: Extract layer (parse snapshot → SourceUserData)
    estimated_steps: 5
  - id: P3
    name: Transform layer (pure functions — planner→Job, file classification, frontmatter)
    estimated_steps: 8
  - id: P4
    name: Load layer (users.toml write, WIKI bootstrap, file copy, Job INSERT)
    estimated_steps: 7
  - id: P5
    name: Report layer + CLI integration
    estimated_steps: 4
  - id: P6
    name: Integration tests + dry-run on real snapshot
    estimated_steps: 3
context_window_budget: ~120k tokens (modules small, well-isolated); fits in single Opus session.
---

# Implementation Plan — Migration ETL (aisw-0a5)

## Phase 1 — Skeleton (P1)

### Task P1.1 — Create migration module skeleton

Создать пустые файлы с MODULE_CONTRACT headers:

```
src/ai_steward_wiki/migration/
├── __init__.py
├── __main__.py
├── config.py
├── extract.py
├── transform.py
├── load.py
└── report.py
```

Каждый файл — header per GRACE convention (FILE, VERSION 0.0.1, MODULE_CONTRACT, MODULE_MAP, CHANGE_SUMMARY). NO logic в P1, только структура.

**TDD**: Создать `tests/unit/migration/__init__.py` + `tests/unit/migration/test_skeleton.py` с failing test `def test_modules_importable(): import ai_steward_wiki.migration.{config,extract,transform,load,report}` → RED → импорт → GREEN.

### Task P1.2 — Add tomli_w dependency

`uv add tomli_w==1.2.0` → проверить `uv.lock` update.

### Task P1.3 — Update .env.example

Добавить блок:
```
# === Migration / Profiles (aisw-0a5) =====================================
# Profile prose files per telegram_id (re-onboarding fills these).
# Empty dir at migration time; populated as users complete onboarding.
AISW_PROFILES_DIR=/home/users/.local/share/ai-steward-wiki/data/profiles
```

### Task P1.4 — Define config.py with USER_MAPPINGS

Хардкод mapping table из Design. Frozen dataclasses (`@dataclass(frozen=True, slots=True)`). Без runtime computation.

Unit tests: `test_config.py`:
- `test_all_user_mappings_unique_telegram_ids`
- `test_admin_count_is_one`
- `test_all_template_ids_valid` (subset of {medical, budget, investment, career, _default})
- `test_drop_dirs_disjoint_from_project_sources`

## Phase 2 — Extract (P2)

### Task P2.1 — `SourcePlannerItem` Pydantic model + planner.json parser

`extract.py::parse_planner_json(path: Path) -> tuple[SourcePlannerItem, ...]`. Validate с pydantic, преобразуя только нужные поля (raw stored для history rendering).

TDD: 4 fixture-planner.json в `tests/unit/migration/fixtures/planners/` (empty, valid-active, valid-history, invalid-multi-recipient) → ожидаемые объекты.

### Task P2.2 — File classification (CSV/JSON/PDF/JPG/MD/script/other)

`extract.py::classify_file(path: Path) -> Literal[...]`. По extension + sanity (not binary in claimed text).

TDD: 8 sample-files → expected types.

### Task P2.3 — Walk user_dir + detect location

`extract.py::walk_user_files(user_dir: Path) -> tuple[SourceFile, ...]`. Determine location: `data/<sub>`, `_output`, `root`, `data` (root of data/), per-project.

TDD: fixture-snapshot с 3 проектами и mixed-content → expected SourceFile list.

### Task P2.4 — Filter DROP_DIRS / DROP_FILE_PATTERNS

При walk skip dev/skeleton dirs и DROP patterns. Logging "drop" decisions.

TDD: fixture с Sensedar/ + ratelimit/ + make_cards.py → они excluded.

### Task P2.5 — `extract_user(mapping, snapshot_root) -> SourceUserData`

Top-level extract. Composes parse_planner + walk_files.

TDD: full user fixture → SourceUserData.

## Phase 3 — Transform (P3, pure functions)

### Task P3.1 — `is_planner_active(item, now_msk_date)`

Active = `status=pending AND (date≥now_msk OR repeat != None)`.

TDD: 5 cases (pending future, pending past one-shot, pending repeat-daily, completed, cancelled).

### Task P3.2 — TZ конверсия MSK→UTC

`msk_to_utc(date_str, time_str, default_time="00:00:00") -> datetime`. ZoneInfo("Europe/Moscow") → UTC.

TDD: DST boundary (March 2026 — no DST in MSK, но проверить).

### Task P3.3 — Category mapping

`map_category(orig: str) -> tuple[Literal["medication","event","generic"], str]`. Returns (new, legacy_category).

TDD: 6 cases (all old enum values + unknown).

### Task P3.4 — Recipients fail-fast

`extract_chat_id(recipients: list[int]) -> int`. len(recipients)!=1 → raise.

TDD: [single], [], [a,b] — последние два raise.

### Task P3.5 — Build cron_expr for monthly

`monthly_to_cron(repeat: dict) -> str`. `{type:monthly, day:D, time:"HH:MM"}` → `"M H D * *"` (5-field).

Validate через `apscheduler.triggers.cron.CronTrigger.from_crontab(expr)` — если raises, transform fail.

TDD: 3 cases (day=1, day=15, day=31; time 00:00, 23:59).

### Task P3.6 — Build Recurrence for daily/weekly

`build_recurrence(repeat: dict, user_tz: str) -> Recurrence`. daily → kind=daily. weekly → kind=weekly + weekdays tuple (mapping mon=0..sun=6).

TDD: 4 cases.

### Task P3.7 — `planner_to_jobs(item, owner_id, wiki_id, user_tz)`

Главная transformation: planner item → list[PlannedJob]. Fan-out по remind_before, monthly → cron_user, daily/weekly → cron_user (с Recurrence сериализованной в cron_expr), one-shot → reminder_job. Always payload.legacy_item_id, payload.legacy_category, payload.legacy_source (if monthly).

PlannedJob = dataclass(frozen=True) с полями (owner_telegram_id, chat_id, kind, status="scheduled", priority, scheduled_at_utc, payload: dict, created_at_utc, user_state="pending", snooze_count=0).

Priority: low→1, none→2, medium→3, high→4. (Map old strings.)

TDD: 12 cases — combinations of (active/inactive) × (one-shot/daily/weekly/monthly) × (remind_before len 1/3) × (category event/medication/task).

### Task P3.8 — File classification → target subpath

`classify_file_target(file: SourceFile, project_mapping) -> FileTarget(rel_target: str)`.

Rules:
- `.py` → DROP
- `.bak`, `notify.json*` → DROP
- file in `data/<sub>/*` and ext in {csv,json,md} → `<wiki>/<sub>/<filename>`
- file in `data/<sub>/*` and ext in {pdf,jpg,jpeg,png} → `<wiki>/raw/<sub>/<filename>`
- file in `data/` directly (no sub) и ext in {csv,json,md} → `<wiki>/<filename>`
- file in `data/` directly и binary → `<wiki>/raw/<filename>`
- file in `_output/*` → `<wiki>/raw/legacy-output/<filename>`
- file in root (not CLAUDE.md/planner.json) → `<wiki>/raw/legacy-root/<filename>`
- `CLAUDE.md` → handled separately (legacy-ai-steward-claude-md.md)

TDD: 12 file path cases.

## Phase 4 — Load (P4, side-effects)

### Task P4.1 — `users.toml` writer

`load.py::write_users_toml(mappings, path, dry_run)`. Использует tomli_w для serialize. Includes schema_version=1 + [[users]] entries (telegram_id, role, display_name, tz, lang).

TDD: write to tmp_path, parse back через `auth.users_toml.load_users_toml`, assert equality.

### Task P4.2 — WIKI bootstrap via WikiLifecycleManager

`load.py::bootstrap_wikis(plan, lifecycle_mgr, dry_run)`. For each (owner, wiki_name, template_id) call `create_wiki(owner=int, raw_name=str, template_id=str)`. Idempotent через built-in `lookup`.

TDD: bootstrap 3 WIKIs под одним owner, проверить FS layout.

### Task P4.3 — File copier (shutil.copy2 preserving mtime)

`load.py::copy_files(plan, dry_run)`. For each (src, dst) pair: `dst.parent.mkdir(parents=True, exist_ok=True)`, `shutil.copy2(src, dst)`. Skip if already exists (idempotent rerun).

TDD: copy 5 mixed-type files, проверить mtime preserved + content identical.

### Task P4.4 — Render & write `legacy-ai-steward-claude-md.md`

`load.py::write_legacy_claude_md(content, target_path, source_path, snapshot_date, dry_run)`. Adds frontmatter:
```
---
source: /home/bgs/ai-steward/<rel>
snapshot_date: YYYY-MM-DD
migration_run_id: aisw-0a5
---

<original content>
```

TDD: round-trip, parse frontmatter.

### Task P4.5 — Render & write `legacy-planner-history.md`

`load.py::write_legacy_history_md(inactive_items, target_path, source_path, ...)`. Markdown sections per item:
```
## <title> [<status>, <category>, <date>]
- Source: planner.json
- ID: <uuid>
- Description: <desc>
- Reminders sent: <sent_reminders>
- (other relevant fields)
```

TDD: 5 inactive items → expected MD.

### Task P4.6 — DB writer: jobs.db INSERT with transaction

`load.py::insert_jobs(planned_jobs, jobs_session_maker, dry_run)`. Один транзакционный batch (`async with session.begin()`). PRAGMA wal_checkpoint(TRUNCATE) до начала. На исключении — rollback + reraise.

TDD: write 10 PlannedJob к temp SQLite, assert SELECT COUNT(*) == 10, payload roundtrip.

### Task P4.7 — pre-ETL snapshot БД

`load.py::snapshot_jobs_db(jobs_db_path, snapshot_path)`. Использует `sqlite3 .backup` API (через Python `sqlite3` stdlib). Skip if dry_run.

TDD: snapshot non-empty DB, restore, verify.

## Phase 5 — Report + CLI (P5)

### Task P5.1 — Aggregate counters in `report.py`

`MigrationCounters` dataclass: per-user dict; per-WIKI dict; total_files_copied; total_jobs_inserted; total_dropped (with reasons).

### Task P5.2 — Render `migration_report.md`

Markdown structure:
1. Header (timestamp, mode, ETL version, source/target paths)
2. Identity table (5 entries)
3. Per-user details (collapsible sections)
4. Drop summary (categories with counts)
5. Warnings / edge cases
6. Next steps (cutover checklist)

TDD: snapshot test against fixture-counters.

### Task P5.3 — `__main__.py` CLI

argparse:
- `--snapshot-root PATH` (required)
- `--target-wiki-root PATH` (default `/home/bgs/.local/share/ai-steward-wiki/workspace/wikis/`)
- `--jobs-db URL` (default from settings)
- `--users-toml PATH` (default `data/users.toml`)
- `--profiles-dir PATH` (default from AISW_PROFILES_DIR or `/home/bgs/.local/share/ai-steward-wiki/data/profiles/`)
- `--report-out PATH` (default stdout)
- `--dry-run` (mutually exclusive with --execute)
- `--execute`

Без флагов или с `--dry-run` only → dry-run. `--execute` → real. asyncio.run(main()).

TDD: invoke CLI with --help, assert exit code 0; invoke без obligatory args → error.

### Task P5.4 — structlog logging anchors

Каждая phase logs:
- `migration.extract.user_done` (telegram_id, planner_items_count, files_count)
- `migration.transform.planner_to_jobs` (item_id, jobs_count, fan_out_n)
- `migration.transform.drop` (path, reason)
- `migration.load.wiki_created` (owner, primary, template_id)
- `migration.load.file_copied` (src, dst)
- `migration.load.jobs_inserted` (owner, count)
- `migration.report.summary` (mode, totals)

## Phase 6 — Integration (P6)

### Task P6.1 — End-to-end dry-run on fixture snapshot

Mini-fixture: 2 users, 3 projects, 1 planner each (3-5 items), 5-10 files. Run --dry-run, assert:
- migration_report.md generated
- No actual writes to DB / FS

### Task P6.2 — End-to-end --execute на отдельном tmp_path

Same fixture, --execute с temp paths. Assert:
- users.toml содержит 2 entries
- jobs.db non-empty
- WIKI dirs created
- Files copied

### Task P6.3 — Manual dry-run на vpn-gpu-1 с реальным snapshot

После всех unit/integration green:
1. `rsync vpn-0:/home/bgs/ai-steward/ /tmp/migration-snapshot/`
2. `rsync /tmp/migration-snapshot/ vpn-gpu-1:/tmp/migration-snapshot/`
3. `ssh vpn-gpu-1 'cd ... && uv run python -m ai_steward_wiki.migration --snapshot-root /tmp/migration-snapshot --dry-run --report-out /tmp/migration_report.md'`
4. Review report manually.

**Не coming в этом плане:** real --execute cutover — это отдельный operator step, требует синхронной координации (stop bots). Отдельно после plan review.

## Self-review checklist

- [x] Every MODULE_CONTRACT (6 modules) → has task(s) in plan (P1.1, P2.1-P2.5, P3.1-P3.8, P4.1-P4.7, P5.1-P5.4)
- [x] Every FR from Discovery → covered:
  - FR-01 → P4.1
  - FR-02 → P4.4
  - FR-03,04,05,06,07 → P3.1-P3.7
  - FR-08,09,10 → P3.8 + P4.3
  - FR-11 → P4.2
  - FR-12 → P4.2 (WikiLifecycleManager per-owner)
  - FR-13 → P4.1 + P1.3 (.env.example)
  - FR-14,15 → P5.3 (CLI flags) + P4.7 (snapshot)
  - FR-16,17 → manual cutover (см. Design)
- [x] Every NFR → has verification step:
  - NFR-01 idempotency → P3.* deterministic + P4.3 already-exists skip + P4.6 transaction
  - NFR-02 observability → P5.4 log anchors + legacy_item_id (P3.7) + P5.2 report
  - NFR-03 safety → P4.7 snapshot + transaction (P4.6)
  - NFR-04 ≤30 min → manual SLA, validated в cutover dry-run (P6.3)
  - NFR-05 coverage ≥80% → unit tests на каждом P*.* TDD step
- [x] verification-plan.xml → derived after execution (grace-refresh --verify in Step 13)
- [x] Log anchors from _logging → enumerated in P5.4
- [x] ADR decisions → нет нового ADR (все решения зафиксированы в Discovery/Design)
- [x] Task order respects DEPENDS → P1 → P2 → P3 → P4 → P5 → P6
- [x] No placeholders → все код-фрагменты конкретны

## Out of scope (not in plan)

1. Real cutover execution — operator step, не code
2. typed Recurrence monthly D-NNN — отдельный follow-up beads
3. systemd unit for new bot — отдельный chore
4. AISW_ENV=local→vps switch — отдельный config task
5. Profile prose ingest — отдельный future feature
