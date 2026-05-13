---
bd_id: aisw-0a5
feature: migrate-ai-steward-userdata
title: Design — Миграция user-data ai-steward → ai-steward-wiki
date: 2026-05-13
status: approved
discovery_ref: 20260513-migrate-ai-steward-userdata-discovery.md
risk: high
risk_justification: см. Discovery
evidence: strong
evidence_justification: ETL-архитектура классическая (Extract-Transform-Load), все целевые API верифицированы в src/ai_steward_wiki/{auth,storage/jobs,wiki}/, Pydantic discriminated union для payload validation на boundary.
open_questions: []
approach: ETL-tool как новый модуль `src/ai_steward_wiki/migration/` + CLI entrypoint `python -m ai_steward_wiki.migration --dry-run | --execute`. SSH snapshot vpn-0 в /tmp/migration-snapshot/ через rsync (manual step), ETL читает оттуда, пишет в production paths vpn-gpu-1.
stack:
  - python: 3.11+
  - sqlalchemy: same as project (orm-async)
  - pydantic: v2 (validation на boundary)
  - tomli_w: для users.toml write (новая dep — добавить)
  - structlog: same as project (JSON logs)
  - pytest: same as project
new_deps:
  - tomli_w==1.2.0 (TOML write; tomllib только для read)
modules:
  - id: M-MIGRATION-CLI
    file: src/ai_steward_wiki/migration/__main__.py
    purpose: CLI entrypoint — argparse, dispatches dry-run vs execute, orchestrates phases.
    depends: [M-MIGRATION-CONFIG, M-MIGRATION-EXTRACT, M-MIGRATION-TRANSFORM, M-MIGRATION-LOAD, M-MIGRATION-REPORT]
  - id: M-MIGRATION-CONFIG
    file: src/ai_steward_wiki/migration/config.py
    purpose: Hardcoded mapping table (telegram_id ↔ source paths ↔ target WIKIs ↔ templates) per Q3 decisions. Single SSoT.
    depends: []
  - id: M-MIGRATION-EXTRACT
    file: src/ai_steward_wiki/migration/extract.py
    purpose: Read snapshot dir, parse planner.json, walk file trees, classify files (CSV/JSON/PDF/JPG/MD/.py/.bak). Returns SourceUserData per telegram_id.
    depends: [M-MIGRATION-CONFIG]
  - id: M-MIGRATION-TRANSFORM
    file: src/ai_steward_wiki/migration/transform.py
    purpose: Pure functions — planner-item → Job + JobPayload (fan-out по remind_before, monthly→cron, category map), file classification → target subpath, frontmatter generation.
    depends: [M-MIGRATION-CONFIG, ai_steward_wiki.storage.jobs.payloads, ai_steward_wiki.classifier.recurrence]
  - id: M-MIGRATION-LOAD
    file: src/ai_steward_wiki/migration/load.py
    purpose: Write users.toml (tomli_w), bootstrap WIKIs via WikiLifecycleManager, INSERT Job rows via SQLAlchemy, copy files (shutil.copy2 preserving mtime), write legacy-planner-history.md.
    depends: [M-MIGRATION-TRANSFORM, ai_steward_wiki.auth.users_toml, ai_steward_wiki.storage.jobs, ai_steward_wiki.wiki.lifecycle]
  - id: M-MIGRATION-REPORT
    file: src/ai_steward_wiki/migration/report.py
    purpose: Aggregate per-user/per-project counters, generate migration_report.md.
    depends: []
ssot_artifacts:
  - .env.example — add AISW_PROFILES_DIR
  - data/users.toml — created at runtime by ETL
  - pyproject.toml — add tomli_w dep
verification:
  - Unit tests для transform.py (12-15 кейсов: fan-out, monthly cron, category, recipients-mismatch fail-fast, date filtering, edge cases)
  - Integration test: дам fixture-snapshot, прогон --dry-run, проверка counters в report
  - Integration test: --execute на тестовом target (отдельный tmp dir + temp SQLite), верификация результата
  - Manual cutover dry-run на staging (smaller snapshot или один user)
---

# Design: Миграция user-data ai-steward → ai-steward-wiki

## Approach

**Классический ETL** (Extract-Transform-Load) с separate CLI tool. Не интегрируется в runtime бота — стоит сбоку как одноразовый migration utility. После миграции код остаётся в репо как reference / для возможного rerun на других данных.

**Архитектурные принципы:**
1. **Pure transformation** — `transform.py` — чистые функции, легко тестируется, deterministic
2. **Side-effects isolated** — `load.py` единственная зона, где DB writes / FS writes
3. **Config-driven** — mapping table (Q3 decision) в одном месте, не размазана по коду
4. **Dry-run safe** — `--dry-run` НИКОГДА не пишет; `--execute` пишет только после явного флага
5. **Strangler-Fig migration** — старый сервис продолжает работать до cutover; cutover атомарный

## Architecture

```
src/ai_steward_wiki/migration/
├── __init__.py
├── __main__.py            # CLI entrypoint
├── config.py              # mapping table (SSoT)
├── extract.py             # snapshot dir → SourceUserData
├── transform.py           # SourceUserData → TargetPlan (pure)
├── load.py                # TargetPlan → DB + FS (side effects)
└── report.py              # counters → migration_report.md

tests/unit/migration/
├── test_config.py
├── test_extract.py
├── test_transform.py
├── test_load.py
└── test_report.py

tests/integration/migration/
└── test_e2e_dry_run.py    # fixture snapshot → dry-run → report verification
```

### Data flow

```
vpn-0:/home/bgs/ai-steward/  (rsync via ssh; manual cutover step)
        ↓
dev-snapshot:/tmp/migration-snapshot/
        ↓ (extract.py)
SourceUserData (Pydantic models, in-memory)
        ↓ (transform.py — pure)
TargetPlan (planned ops: jobs, files, wikis, profiles, report)
        ↓ (load.py — side effects, gated by --execute)
production:/home/bgs/works/ai-steward-wiki/data/ + /home/bgs/.local/share/ai-steward-wiki/workspace/wikis/
        + migration_report.md
```

## Module sketches

### M-MIGRATION-CONFIG (`config.py`)

```python
@dataclass(frozen=True)
class UserMapping:
    telegram_id: int
    source_dir: Path           # rel to snapshot root: e.g. "Gena_Beeline_VPN-0"
    display_name: str
    role: Literal["admin", "user"]
    tz: str = "Europe/Moscow"
    lang: str = "ru"
    projects: tuple[ProjectMapping, ...]

@dataclass(frozen=True)
class ProjectMapping:
    source_project: str | None  # None = root-level (planner.json в корне юзера)
    target_wiki: str            # raw name pre-normalize: "Medical", "Budget", ...
    template_id: str            # "medical", "budget", "investment", "career", "_default"

USER_MAPPINGS: tuple[UserMapping, ...] = (
    UserMapping(763463467, Path("Gena_Beeline_VPN-0"), "Геннадий", "admin",
        projects=(
            ProjectMapping("Health", "Medical", "medical"),
            ProjectMapping("Expenses", "Budget", "budget"),
            ProjectMapping("investment", "Investment", "investment"),
            ProjectMapping("2025_Noveo", "Career", "career"),
            ProjectMapping("NOVEO-ORANGE", "Career", "career"),
            ProjectMapping(None, "Default", "_default"),
        ),
    ),
    UserMapping(6156629438, Path("Gena_MTS"), "Геннадий (MTS)", "user",
        projects=(ProjectMapping("Noveo", "Career", "career"),),
    ),
    UserMapping(1151678530, Path("Tania"), "Татьяна", "user",
        projects=(
            ProjectMapping("Health", "Medical", "medical"),
            ProjectMapping("Weightwatch", "Weightwatch", "_default"),
        ),
    ),
    UserMapping(1122408606, Path("Dari"), "Дари", "user", projects=()),
    UserMapping(8587590035, Path("Marat"), "Марат", "user",
        projects=(ProjectMapping(None, "Default", "_default"),),
    ),
)

DROP_DIRS = frozenset({  # dev-code / skeleton — never migrate
    "Sensedar", "python-ai-skills", "Claude-bot", "prognosis",
    "test_fastapi", "scripts", "Fashionista", "ratelimit",
})

DROP_FILE_PATTERNS = ("*.py", "*.bak", "notify.json*")

CATEGORY_MAP = {
    "medication": "medication",
    "event": "event",
    "task": "generic",
    "reminder": "generic",
    "todo": "generic",
    "block": "generic",
}
```

### M-MIGRATION-EXTRACT (`extract.py`)

```python
@dataclass(frozen=True)
class SourcePlannerItem:
    item_id: str              # original UUID
    title: str
    description: str
    category: str
    priority: str
    date: str                 # YYYY-MM-DD (МСК)
    time_start: str | None
    remind_before: list[int]
    repeat: dict | None
    recipients: list[int]
    status: str
    project: str | None       # None = root
    raw: dict                 # full original for history rendering

@dataclass(frozen=True)
class SourceFile:
    abs_path: Path
    rel_path: str             # relative to user_dir
    project: str | None
    location: Literal["data", "_output", "root", "data/<subfolder>"]
    file_type: Literal["csv", "json", "md", "pdf", "jpg", "txt", "other"]

@dataclass(frozen=True)
class SourceUserData:
    telegram_id: int
    user_dir: Path
    planner_items: tuple[SourcePlannerItem, ...]
    files: tuple[SourceFile, ...]

def extract_user(mapping: UserMapping, snapshot_root: Path) -> SourceUserData:
    """Walk user_dir, parse planner.json files, classify files."""
```

### M-MIGRATION-TRANSFORM (`transform.py`)

Pure functions (no IO):

```python
def is_planner_active(item: SourcePlannerItem, now_msk: date) -> bool:
    """status=pending AND (date≥now OR repeat≠null)."""

def planner_to_jobs(
    item: SourcePlannerItem,
    *,
    owner_telegram_id: int,
    wiki_id: str,
    user_tz: ZoneInfo,
) -> list[PlannedJob]:
    """Fan-out по remind_before. Monthly → cron_user. Daily/weekly → cron_user
    + Recurrence. One-shot → reminder_job. Category map + legacy_category.
    Recipients[0] всегда, fail-fast при len>1.
    Returns 1..N PlannedJob rows."""

def classify_file_target(file: SourceFile, project_mapping: ProjectMapping) -> FileTarget:
    """CSV/JSON in data/<subfolder> → <wiki>/<subfolder>/. PDF/JPG → raw/<subfolder>/.
    _output/* → raw/legacy-output/. root binary → raw/legacy-root/. .py → drop."""

def render_legacy_history_md(
    items: list[SourcePlannerItem],
    *,
    source_path: str,
    snapshot_date: str,
) -> str:
    """Render Markdown summary of inactive planner items per source planner.json."""

def render_legacy_claude_md(
    original_content: str,
    *,
    source_path: str,
    snapshot_date: str,
) -> str:
    """Wrap original CLAUDE.md content with frontmatter (source path, snapshot date)."""
```

### M-MIGRATION-LOAD (`load.py`)

Side-effects, gated:

```python
class MigrationLoader:
    def __init__(self, *, target_wiki_root: Path, jobs_db_url: str, users_toml_path: Path,
                 profiles_dir: Path, dry_run: bool):
        self._dry_run = dry_run
        # ... init lifecycle manager, jobs session, ...

    async def execute(self, plan: TargetPlan) -> LoadReport:
        # 0. Snapshot jobs.db → /tmp/jobs.db.pre-migration.{ts}
        # 1. Write users.toml (tomli_w)
        # 2. mkdir profiles_dir
        # 3. For each (owner, wiki_raw_name, template_id) in plan: WikiLifecycleManager.create_wiki()
        # 4. For each file in plan.files: shutil.copy2(src, dst)
        # 5. For each (wiki_path, legacy_md_content) in plan.legacy_history_md: write file
        # 6. PRAGMA wal_checkpoint(TRUNCATE) on jobs.db
        # 7. INSERT BATCH Jobs (transaction)
        # 8. Return LoadReport (counts)
```

### M-MIGRATION-REPORT (`report.py`)

```python
def render_migration_report(
    plan: TargetPlan, *, dry_run: bool, snapshot_date: str,
) -> str:
    """Markdown report:
    - Header: snapshot date, dry-run/execute mode, ETL version
    - Per-user table: telegram_id, display_name, projects, WIKIs created, planner active, history items, files copied
    - Per-WIKI breakdown
    - Drop summary: which dirs/files dropped and why
    - Edge cases / warnings
    - Failed transformations (if any) — in execute mode this would have failed Fast
    """
```

### M-MIGRATION-CLI (`__main__.py`)

```bash
# Dry-run (no writes)
uv run python -m ai_steward_wiki.migration \
    --snapshot-root /tmp/migration-snapshot/ \
    --target-wiki-root /home/bgs/.local/share/ai-steward-wiki/workspace/wikis/ \
    --jobs-db sqlite+aiosqlite:////home/bgs/works/ai-steward-wiki/data/jobs.db \
    --users-toml /home/bgs/works/ai-steward-wiki/data/users.toml \
    --profiles-dir /home/bgs/.local/share/ai-steward-wiki/data/profiles/ \
    --report-out /tmp/migration_report.md \
    --dry-run

# Real execute (gated explicitly)
uv run python -m ai_steward_wiki.migration ... --execute
```

## Cutover sequence (manual operator script)

```bash
# 0. Создать pre-cutover snapshot БД нового бота
ssh vpn-gpu-1 'sqlite3 /home/bgs/works/ai-steward-wiki/data/jobs.db ".backup /tmp/jobs.db.pre-migration.$(date +%s)"'

# 1. Остановить старого бота на vpn-0
ssh vpn-0 'sudo systemctl stop ai-steward'  # или pkill, зависит от deploy

# 2. Остановить нового бота на vpn-gpu-1
ssh vpn-gpu-1 'pkill -f "python -m ai_steward_wiki"'

# 3. Snapshot vpn-0 → dev-machine
rsync -aH --delete vpn-0:/home/bgs/ai-steward/ /tmp/migration-snapshot/

# 4. Скопировать snapshot на vpn-gpu-1 dev workspace
rsync -aH /tmp/migration-snapshot/ vpn-gpu-1:/tmp/migration-snapshot/

# 5. Dry-run на vpn-gpu-1
ssh vpn-gpu-1 'cd /home/bgs/works/ai-steward-wiki && uv run python -m ai_steward_wiki.migration --snapshot-root /tmp/migration-snapshot --dry-run --report-out /tmp/migration_report.md'

# 6. Review /tmp/migration_report.md → если ок продолжаем

# 7. Real execute
ssh vpn-gpu-1 'cd /home/bgs/works/ai-steward-wiki && uv run python -m ai_steward_wiki.migration --snapshot-root /tmp/migration-snapshot --execute'

# 8. Restart нового бота
ssh vpn-gpu-1 'cd /home/bgs/works/ai-steward-wiki && nohup uv run python -m ai_steward_wiki > /tmp/aisw.log 2>&1 &'

# 9. Sanity-check: 5 юзеров in users.toml, jobs.db non-empty, wikis exist
# 10. Announce юзерам — переключение на нового бота
```

## Trade-offs & decisions

1. **`tomli_w` new dep vs string-templating** — tomli_w безопаснее (escaping, типы), стандарт для TOML write. ~50 KB dep. Acceptable.
2. **Per-step transaction vs single batch** — single transaction для Jobs INSERT (rollback на ошибке). Файловые операции — best-effort: при ошибке копирования файла Fail-Fast после snapshot БД (rollback БД, FS остаётся в частично записанном состоянии, manual cleanup).
3. **dataclass vs Pydantic для internal models** — dataclass(frozen=True) достаточно для in-memory, Pydantic только на boundary с external schemas (planner.json items, Job/JobPayload).
4. **CLI: argparse vs typer/click** — argparse stdlib, нет new dep, очень простой CLI (5 flags).
5. **Async vs sync** — async для SQLAlchemy (как остальной проект), но extract/transform могут быть sync. CLI = async main(), синхронные части просто await-ятся.

## Open questions

(пусто — все технические развилки разрешены, см. Q1-Q8 transcript)
