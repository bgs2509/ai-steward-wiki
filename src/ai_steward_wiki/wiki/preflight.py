# FILE: src/ai_steward_wiki/wiki/preflight.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: 5-step pre-flight grounding per D-041 (locks, frontmatter,
#            template, staging size, write-area permissions).
#   SCOPE: PreflightCheck, PreflightReport, preflight() entry point.
#   DEPENDS: ai_steward_wiki.wiki.migration, pydantic
#   LINKS: M-WIKI-LIFECYCLE, D-041, tech-spec §4 "5-шаговый pre-flight"
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PreflightCheck - frozen Pydantic (name, ok, detail)
#   PreflightReport - frozen Pydantic (checks tuple, ok aggregate)
#   preflight - run all 5 steps, return PreflightReport
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 8: 5-step pre-flight scaffold
# END_CHANGE_SUMMARY

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ai_steward_wiki.wiki.migration import FrontmatterError, parse_frontmatter

CheckName = Literal["locks", "frontmatter", "template", "staging", "permissions"]


class PreflightCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CheckName
    ok: bool
    detail: str


class PreflightReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    checks: tuple[PreflightCheck, ...]
    ok: bool


def _default_lock_probe(wiki_path: Path) -> bool:
    """Default: pass if no .wiki.lock file exists. (Stale-PID recovery is done
    by scheduler.locks during actual acquire; pre-flight only needs a coarse
    signal that no live session currently owns the wiki.)"""
    return not (wiki_path / ".wiki.lock").exists()


def preflight(
    *,
    wiki_path: Path,
    template_dir: Path,
    staging_dir: Path | None = None,
    max_staging_bytes: int = 100 * 1024 * 1024,
    lock_probe: Callable[[Path], bool] | None = None,
) -> PreflightReport:
    """Run all 5 grounding checks and aggregate."""
    probe = lock_probe if lock_probe is not None else _default_lock_probe
    checks: list[PreflightCheck] = []

    # 1. locks
    locks_ok = probe(wiki_path)
    checks.append(
        PreflightCheck(
            name="locks",
            ok=locks_ok,
            detail="lock probe ok" if locks_ok else "lock held (.wiki.lock present)",
        )
    )

    # 2. frontmatter
    claude_md = wiki_path / "CLAUDE.md"
    fm_ok = False
    fm_detail = ""
    template_id = ""
    if not claude_md.exists():
        fm_detail = "CLAUDE.md missing"
    else:
        try:
            fm, _ = parse_frontmatter(claude_md.read_text(encoding="utf-8"))
            template_id = fm.template_id
            if fm.schema_version == 2:
                fm_ok = True
                fm_detail = "schema_version=2"
            else:
                fm_detail = f"schema_version={fm.schema_version}, expected 2"
        except FrontmatterError as exc:
            fm_detail = f"parse error: {exc}"
    checks.append(PreflightCheck(name="frontmatter", ok=fm_ok, detail=fm_detail))

    # 3. template
    if not template_id:
        tpl_ok, tpl_detail = False, "no template_id in frontmatter"
    else:
        candidate_paths = [
            template_dir / f"{template_id}.md",
            template_dir / template_id / "CLAUDE.md",
        ]
        found = next((p for p in candidate_paths if p.exists()), None)
        if found is not None:
            tpl_ok, tpl_detail = True, f"template found at {found}"
        else:
            tpl_ok, tpl_detail = False, f"template_id={template_id!r} not found in {template_dir}"
    checks.append(PreflightCheck(name="template", ok=tpl_ok, detail=tpl_detail))

    # 4. staging
    if staging_dir is None:
        stg_ok, stg_detail = True, "no staging dir to check"
    elif not staging_dir.exists():
        stg_ok, stg_detail = True, "staging dir absent"
    else:
        total = sum(p.stat().st_size for p in staging_dir.rglob("*") if p.is_file())
        if total <= max_staging_bytes:
            stg_ok = True
            stg_detail = f"{total} bytes <= {max_staging_bytes}"
        else:
            stg_ok = False
            stg_detail = f"{total} bytes > {max_staging_bytes}"
    checks.append(PreflightCheck(name="staging", ok=stg_ok, detail=stg_detail))

    # 5. permissions
    if not wiki_path.exists():
        perm_ok, perm_detail = False, "wiki_path missing"
    else:
        readable = os.access(wiki_path, os.R_OK)
        writable = os.access(wiki_path, os.W_OK)
        perm_ok = readable and writable
        perm_detail = f"readable={readable}, writable={writable}"
    checks.append(PreflightCheck(name="permissions", ok=perm_ok, detail=perm_detail))

    return PreflightReport(checks=tuple(checks), ok=all(c.ok for c in checks))
