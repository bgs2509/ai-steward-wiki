# Design — M-WIKI-LIFECYCLE

**Discovery:** `20260510-wiki-lifecycle-discovery.md` (stable).
**Status:** stable.
**Date:** 2026-05-10.

## Module layout

```
src/ai_steward_wiki/wiki/
├── __init__.py     # extended MODULE_MAP (BARREL)
├── acquire.py      # (chunk 7) lock acquirer Protocol
├── streaming.py    # (chunk 7) stream-json parser
├── runner.py       # (chunk 7) Stage-1a/1b orchestrator
├── name.py         # NEW — Cyrillic→Latin ISO 9, PascalCase, WikiName
├── lifecycle.py    # NEW — WikiLifecycleManager (create/lookup/soft-delete/restore)
├── preflight.py    # NEW — 5-step pre-flight grounding
└── migration.py    # NEW — frontmatter parser + v1→v2 linear migrator
```

## Types

```python
class WikiName(BaseModel):                       # frozen
    primary: str                                 # e.g. "HealthLite-WIKI"
    hyphenated_lookup: str                       # e.g. "multi-word"
    slug: str                                    # e.g. "healthlite"

class Frontmatter(BaseModel):                    # frozen
    schema_version: int                          # 1 or 2
    template_id: str
    last_migrated_at: str                        # ISO 8601 UTC
    template_sha256: str

class PreflightCheck(BaseModel):                 # frozen
    name: Literal["locks","frontmatter","template","staging","permissions"]
    ok: bool
    detail: str

class PreflightReport(BaseModel):                # frozen
    checks: tuple[PreflightCheck, ...]
    ok: bool

class TrashedWiki(BaseModel):                    # frozen
    primary: str
    trashed_path: Path
    deleted_at: str                              # ISO 8601 UTC

class NearDuplicateMatch(BaseModel):             # frozen
    existing_primary: str
    distance: int
```

## ISO 9 transliteration (`name.py`)

Static mapping `_ISO9: dict[str, str]` for the 33 lower-case Russian letters
+ their upper-case forms. Multi-character outputs allowed (`ж → zh`,
`ц → cz`, `ш → sh`, `щ → shh`, `ю → yu`, `я → ya`, etc. per ISO 9:1995
romanisation table — single-strategy, no diacritics).

```python
def normalize_wiki_name(raw: str) -> WikiName:
    """ISO9 → split non-alnum → PascalCase → -WIKI suffix → validate."""
```

Validation regex: `^[A-Z][A-Za-z0-9]*-WIKI$`. Empty / pure-punctuation input
raises `WikiNameError`.

`hyphenated_lookup` = lower-case slug with single hyphen between camel
boundaries: `MultiWord-WIKI → multi-word`. `slug` = lower-case primary
without `-WIKI` and without hyphens (`healthlite`).

## Levenshtein (`lifecycle.py`)

Classic two-row DP, pure-Python:

```python
def _levenshtein(a: str, b: str) -> int:
    ...
```

Called only on the lower-case slug portion. Threshold ≤2 per D-041.

## WikiLifecycleManager API

```python
class WikiLifecycleManager:
    def __init__(self, wiki_root: Path, *, max_per_user: int = 20) -> None: ...
    def list_active(self, owner: int) -> list[WikiName]: ...
    def list_trashed(self, owner: int) -> list[TrashedWiki]: ...
    def create_wiki(self, owner: int, raw_name: str, template_id: str) -> WikiName: ...
    def lookup(self, owner: int, name_or_hyphenated: str) -> WikiName | None: ...
    def soft_delete(self, owner: int, primary: str) -> TrashedWiki: ...
    def restore(self, owner: int, trashed: TrashedWiki, *, now_utc: datetime | None = None) -> WikiName: ...
```

Layout per owner:
```
<wiki_root>/<owner>/<Name>-WIKI/CLAUDE.md
<wiki_root>/<owner>/_trash/<UTC-ts>_<Name>-WIKI/
```

`_trash/` is excluded from `list_active` and from the cap counter and from
Levenshtein comparison only when explicitly requested via `include_trash=False`
(default for cap check). For name-collision-on-restore, the manager checks
against `_trash/` as well (per spec §5 "Levenshtein ... включая `_trash/`").

Atomicity: directory rename via `os.replace` (same FS guarantee). Trash
timestamp format: `YYYYMMDDTHHMMSSZ` (UTC).

Create flow:
1. `normalize_wiki_name(raw_name)`.
2. If primary already exists for owner → return existing `WikiName` (idempotent).
3. Cap check: `len(list_active(owner)) >= max_per_user` → `AntiSpamCapError`.
4. Levenshtein scan against active + trash slugs; min distance ≤2 → return
   existing (`NearDuplicateMatch` attached via raise-with-payload? — simpler:
   return the existing `WikiName`, log `wiki.lifecycle.near_duplicate`).
5. Create directory + skeleton `CLAUDE.md` with v2 frontmatter.

## Pre-flight (`preflight.py`)

```python
def preflight(
    *, owner: int, wiki_path: Path, template_dir: Path,
    staging_dir: Path | None = None, max_staging_bytes: int = 100 * 1024 * 1024,
    lock_probe: Callable[[Path], bool] | None = None,
) -> PreflightReport:
```

Steps (each adds a `PreflightCheck`):
1. **locks** — call `lock_probe(wiki_path)` (default: check absence of stale
   `.wiki.lock` PID file). `ok=False` if held by another live PID.
2. **frontmatter** — parse `wiki_path/CLAUDE.md` frontmatter; `ok=True` iff
   `schema_version` is 2 (current).
3. **template** — verify `template_id` from frontmatter resolves to a file in
   `template_dir`. `ok=False` if missing.
4. **staging** — if `staging_dir` provided, recursive size ≤ `max_staging_bytes`.
5. **permissions** — `wiki_path` exists, readable, writable by current uid.

`PreflightReport.ok = all(c.ok for c in checks)`.

## Migration (`migration.py`)

```python
MANAGED_START = "<!-- managed:start -->"
MANAGED_END   = "<!-- managed:end -->"
USER_START    = "<!-- user:start -->"
USER_END      = "<!-- user:end -->"

def parse_frontmatter(text: str) -> tuple[Frontmatter, str]: ...
def render_frontmatter(fm: Frontmatter) -> str: ...
def extract_user_zone(body: str) -> str | None: ...
def render_v2(*, fm: Frontmatter, managed: str, user: str) -> str: ...
def migrate_v1_to_v2(path: Path, *, template_managed: str, template_sha256: str,
                     template_id: str | None = None,
                     now_utc: datetime | None = None) -> bool: ...
```

`migrate_v1_to_v2` returns `True` if migration was applied, `False` if
already v2 (idempotent no-op). Atomic write via `tmp` sibling + `os.replace`.

Frontmatter format (strict subset, YAML-like — no nested mappings):

```
---
schema_version: 2
template_id: health
last_migrated_at: 2026-05-10T12:34:56Z
template_sha256: abc123…
---
```

User-zone in v1 is identified heuristically as all content after the last
`<!-- user:start -->` marker, or — if no markers existed in v1 — the entire
body (worst case preserves everything verbatim, which is the safe default).

## Settings extension

```python
# settings.py additions
wiki_root: Path = Path("/var/lib/ai-steward-wiki/workspace/wikis")
wiki_max_per_user: int = 20
wiki_trash_retention_days: int = 30
wiki_template_dir: Path = Path("/opt/ai-steward-wiki/templates")
```

## Tests (`tests/unit/wiki/lifecycle/`)

1. `conftest.py` — `wiki_root` tmp_path fixture; `template_dir` with minimal
   `health.md` + `_default.md`.
2. `test_name.py` — Cyrillic table (ж, ц, ш, щ, ю, я, ь, ъ); `Здоровье` →
   `Zdorove-WIKI`; `health lite` → `HealthLite-WIKI`; bad regex rejected;
   `hyphenated_lookup` & `slug` shapes.
3. `test_lifecycle.py` — cap=2 enforcement; Levenshtein near-match returns
   existing; soft-delete moves to `_trash/<ts>_…`; restore within window;
   trash excluded from active list and cap.
4. `test_preflight.py` — each of 5 checks pass/fail with fixtures.
5. `test_migration.py` — v1 doc with user content preserved verbatim; idempotent
   no-op when already v2; atomic (tmp gone, target updated); schema_version
   bumped; managed-zone refreshed from template.

## scripts/lint_invariants.py

Recursive grep over `src/` excluding `wiki/lifecycle.py`. Looks for forbidden
patterns: `shutil.rmtree`, `os.rmdir`, `Path.unlink`, `rm -rf`, `mv ` on wiki
paths. Exit-1 on any hit outside the allowlist. INV-7 evidence file.

## Out of scope (this chunk)

1. APScheduler trash-purge job (chunk 13).
2. systemd-run wrapping (chunk 16).
3. Template catalogue (chunk 15).
