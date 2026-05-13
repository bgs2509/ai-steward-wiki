# FILE: src/ai_steward_wiki/migration/extract.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Phase E of ETL. Read snapshot dir (rsync of /home/bgs/ai-steward/),
#            parse planner.json files, walk file trees, classify files. No
#            transformation logic here — only "read & classify into in-memory
#            SourceUserData". Pure side-effect-free w.r.t. target paths.
#   SCOPE: SourcePlannerItem, SourceFile, SourceUserData, FileLocation,
#          FileType, parse_planner_json, classify_file, walk_user_files,
#          extract_user, extract_all.
#   DEPENDS: pydantic v2, ai_steward_wiki.migration.config
#   LINKS: M-MIGRATION-EXTRACT, aisw-0a5
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   FileLocation - Literal: 'data_root' | 'data_subfolder' | 'output' | 'user_root' | 'project_root'
#   FileType - Literal: 'csv' | 'json' | 'md' | 'pdf' | 'image' | 'text' | 'script' | 'binary' | 'other'
#   SourcePlannerItem - one parsed item from planner.json (frozen Pydantic)
#   SourceFile - one file with classified location + type
#   SourceUserData - composite per telegram_id (planner items + files)
#   parse_planner_json - read .json -> tuple[SourcePlannerItem, ...] (or empty)
#   classify_file - Path -> FileType by extension
#   walk_user_files - walk user_dir -> tuple[SourceFile, ...] (honors DROP_DIRS/PATTERNS)
#   extract_user - compose parse + walk for one UserMapping
#   extract_all - extract every UserMapping with logging
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-0a5 P2.1-P2.5: snapshot parser + file walker
# END_CHANGE_SUMMARY

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from ai_steward_wiki.migration.config import (
    DROP_DIRS,
    DROP_FILE_PATTERNS,
    USER_MAPPINGS,
    UserMapping,
)

__all__ = [
    "FileLocation",
    "FileType",
    "SourceFile",
    "SourcePlannerItem",
    "SourceUserData",
    "classify_file",
    "extract_all",
    "extract_user",
    "parse_planner_json",
    "walk_user_files",
]

_log = structlog.get_logger(__name__)


FileLocation = Literal[
    "data_root",  # <project>/data/<file> (direct, no subfolder)
    "data_subfolder",  # <project>/data/<sub>/<file>
    "output",  # <project>/_output/<file>
    "user_root",  # <user>/<file>     (no specific project — e.g. Marat/20260225_*.jpg)
    "project_root",  # <project>/<file> (e.g. Gena/Expenses/Квитанция-01-2026.pdf)
]

FileType = Literal[
    "csv",
    "json",
    "md",
    "pdf",
    "image",
    "text",
    "script",
    "binary",
    "other",
]

# Extension -> FileType.
_EXT_TYPE: dict[str, FileType] = {
    ".csv": "csv",
    ".json": "json",
    ".md": "md",
    ".pdf": "pdf",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".webp": "image",
    ".txt": "text",
    ".py": "script",
    ".sh": "script",
}


# START_BLOCK_PYDANTIC_MODELS
class SourcePlannerItem(BaseModel):
    """One item parsed from legacy planner.json.

    Only fields used in transformation are typed; full `raw` dict is kept
    for `legacy-planner-history.md` rendering (Q1.3.A).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    item_id: str = Field(alias="id")
    title: str
    description: str = ""
    category: str
    priority: str = "none"
    date: str  # YYYY-MM-DD (MSK), required
    time_start: str | None = None
    time_end: str | None = None
    deadline: str | None = None
    remind_before: list[int] = Field(default_factory=lambda: [0])
    repeat: dict[str, object] | None = None
    recipients: list[int]
    status: str = "pending"
    sent_reminders: list[object] = Field(default_factory=list)
    project: str | None = None  # injected by parser, not from JSON
    raw: dict[str, object] = Field(default_factory=dict)  # full original


# END_BLOCK_PYDANTIC_MODELS


# START_BLOCK_SOURCE_FILE
@dataclass(frozen=True, slots=True)
class SourceFile:
    """One file discovered in the snapshot.

    `abs_path` is absolute on the local FS (snapshot root unpacked).
    `rel_to_user` is relative to the user_dir (e.g. "Health/data/metrics/weight.csv").
    """

    abs_path: Path
    rel_to_user: str
    project: str | None  # None = user-root level
    location: FileLocation
    file_type: FileType
    subfolder: str | None  # for data_subfolder: e.g. "metrics", "prescriptions"; else None


# END_BLOCK_SOURCE_FILE


# START_BLOCK_SOURCE_USER_DATA
@dataclass(frozen=True, slots=True)
class SourceUserData:
    telegram_id: int
    user_dir: Path
    planner_items: tuple[SourcePlannerItem, ...]
    files: tuple[SourceFile, ...]
    claude_md_files: tuple[Path, ...]  # CLAUDE.md (user + per-project)


# END_BLOCK_SOURCE_USER_DATA


# START_CONTRACT: classify_file
#   PURPOSE: Map a file path to a FileType based on extension.
#   INPUTS: { path: Path }
#   OUTPUTS: { FileType - one of the closed Literal values }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-EXTRACT
# END_CONTRACT: classify_file
def classify_file(path: Path) -> FileType:
    ext = path.suffix.lower()
    return _EXT_TYPE.get(ext, "other")


# START_CONTRACT: parse_planner_json
#   PURPOSE: Read a legacy planner.json file and return parsed items.
#   INPUTS: { path: Path, project: str | None - tag injected into each item }
#   OUTPUTS: { tuple[SourcePlannerItem, ...] - empty if file is empty/no items }
#   SIDE_EFFECTS: reads the file
#   LINKS: M-MIGRATION-EXTRACT, planner.json schema (CLAUDE.md spec)
# END_CONTRACT: parse_planner_json
def parse_planner_json(path: Path, *, project: str | None) -> tuple[SourcePlannerItem, ...]:
    if not path.exists():
        return ()
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return ()
    data = json.loads(raw)
    items_raw = data.get("items", []) if isinstance(data, dict) else []
    out: list[SourcePlannerItem] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        out.append(SourcePlannerItem.model_validate({**it, "project": project, "raw": it}))
    return tuple(out)


def _should_drop_file(path: Path) -> bool:
    name = path.name
    return any(fnmatch.fnmatch(name, pat) for pat in DROP_FILE_PATTERNS)


def _classify_location(
    rel_parts: tuple[str, ...], project: str | None
) -> tuple[FileLocation, str | None]:
    """Map a tuple of path parts (rel to user_dir) -> (FileLocation, subfolder).

    rel_parts example: ("Health", "data", "metrics", "weight.csv") -> ("data_subfolder", "metrics")
    rel_parts example: ("Health", "data", "expenses.csv") -> ("data_root", None)
    rel_parts example: ("Health", "_output", "report.md") -> ("output", None)
    rel_parts example: ("Health", "Квитанция.pdf") -> ("project_root", None)
    rel_parts example: ("photo.jpg",) -> ("user_root", None)
    """
    if project is None:
        return ("user_root", None)
    # rel_parts[0] is the project dir; rest is inside it.
    rest = rel_parts[1:]
    if not rest:
        # Should not happen — we only call with file paths, not project dirs.
        return ("project_root", None)
    head = rest[0]
    if head == "data":
        if len(rest) == 2:
            # data/<file>
            return ("data_root", None)
        # data/<sub>/.../<file> — we collapse arbitrary depth into 1st subfolder
        return ("data_subfolder", rest[1])
    if head == "_output":
        return ("output", None)
    # something else inside project root (e.g. Квитанция.pdf at Expenses/)
    return ("project_root", None)


# START_CONTRACT: walk_user_files
#   PURPOSE: Walk a user_dir and produce SourceFile entries for every file
#            that survives DROP_DIRS / DROP_FILE_PATTERNS / known-noise rules.
#            Excludes CLAUDE.md and planner.json (handled separately) AND
#            also returns the discovered CLAUDE.md paths.
#   INPUTS: { user_dir: Path }
#   OUTPUTS: { tuple[tuple[SourceFile, ...], tuple[Path, ...]] -
#              (files, claude_md_paths) }
#   SIDE_EFFECTS: reads FS (stat + iterdir)
#   LINKS: M-MIGRATION-EXTRACT
# END_CONTRACT: walk_user_files
def walk_user_files(
    user_dir: Path,
) -> tuple[tuple[SourceFile, ...], tuple[Path, ...]]:
    files: list[SourceFile] = []
    claude_md: list[Path] = []

    if not user_dir.exists() or not user_dir.is_dir():
        return ((), ())

    for entry in sorted(user_dir.iterdir()):
        if entry.is_file():
            # User-root level files
            if entry.name in {"CLAUDE.md", "planner.json"}:
                if entry.name == "CLAUDE.md":
                    claude_md.append(entry)
                continue
            if _should_drop_file(entry):
                _log.info("migration.extract.drop", path=str(entry), reason="pattern")
                continue
            files.append(
                SourceFile(
                    abs_path=entry,
                    rel_to_user=entry.name,
                    project=None,
                    location="user_root",
                    file_type=classify_file(entry),
                    subfolder=None,
                )
            )
            continue

        if not entry.is_dir():
            continue

        # Skip _output at user-root level (it's per-project per legacy convention,
        # but user-root _output also appears sometimes — treat as user-root
        # output regardless of structure).
        project_name = entry.name
        if project_name in DROP_DIRS:
            _log.info("migration.extract.drop", path=str(entry), reason="drop_dir")
            continue

        # Walk into project_name recursively, classify each file.
        _walk_project(entry, project_name, files, claude_md)

    return (tuple(files), tuple(claude_md))


def _walk_project(
    project_dir: Path,
    project_name: str,
    out_files: list[SourceFile],
    out_claude_md: list[Path],
) -> None:
    for sub in sorted(project_dir.rglob("*")):
        if sub.is_dir():
            # Skip nested DROP_DIRS (e.g. Gena/Expenses/scripts/ — none today,
            # but defence in depth).
            if sub.name in DROP_DIRS:
                _log.info("migration.extract.drop", path=str(sub), reason="drop_dir_nested")
                # rglob still yields children — we filter individually below.
                continue
            continue

        if not sub.is_file():
            continue

        # Filter children of dropped subdirs
        if any(part in DROP_DIRS for part in sub.relative_to(project_dir).parts):
            continue

        if sub.name == "planner.json":
            # planner.json handled in extract_user; skip here.
            continue
        if sub.name == "CLAUDE.md":
            out_claude_md.append(sub)
            continue
        if _should_drop_file(sub):
            _log.info("migration.extract.drop", path=str(sub), reason="pattern")
            continue

        rel_parts = sub.relative_to(project_dir.parent).parts
        location, subfolder = _classify_location(rel_parts, project_name)
        out_files.append(
            SourceFile(
                abs_path=sub,
                rel_to_user=str(sub.relative_to(project_dir.parent)),
                project=project_name,
                location=location,
                file_type=classify_file(sub),
                subfolder=subfolder,
            )
        )


# START_CONTRACT: extract_user
#   PURPOSE: Build SourceUserData for one UserMapping. Reads planner.json
#            from user root AND from every project subdir; walks files.
#   INPUTS: { mapping: UserMapping, snapshot_root: Path }
#   OUTPUTS: { SourceUserData - frozen dataclass with all the user's source content }
#   SIDE_EFFECTS: reads FS
#   LINKS: M-MIGRATION-EXTRACT
# END_CONTRACT: extract_user
def extract_user(mapping: UserMapping, *, snapshot_root: Path) -> SourceUserData:
    user_dir = snapshot_root / mapping.source_dir

    files, claude_md = walk_user_files(user_dir)

    planner_items: list[SourcePlannerItem] = []
    # Root-level planner (may not exist for most users).
    root_planner = user_dir / "planner.json"
    if root_planner.exists():
        planner_items.extend(parse_planner_json(root_planner, project=None))

    # Per-project planner.json.
    for entry in sorted(user_dir.iterdir() if user_dir.exists() else ()):
        if not entry.is_dir() or entry.name in DROP_DIRS:
            continue
        proj_planner = entry / "planner.json"
        if proj_planner.exists():
            planner_items.extend(parse_planner_json(proj_planner, project=entry.name))

    _log.info(
        "migration.extract.user_done",
        telegram_id=mapping.telegram_id,
        planner_items=len(planner_items),
        files=len(files),
        claude_md=len(claude_md),
    )
    return SourceUserData(
        telegram_id=mapping.telegram_id,
        user_dir=user_dir,
        planner_items=tuple(planner_items),
        files=files,
        claude_md_files=claude_md,
    )


# START_CONTRACT: extract_all
#   PURPOSE: Convenience wrapper — extract every UserMapping from USER_MAPPINGS.
#   INPUTS: { snapshot_root: Path }
#   OUTPUTS: { tuple[SourceUserData, ...] }
#   SIDE_EFFECTS: reads FS
#   LINKS: M-MIGRATION-EXTRACT
# END_CONTRACT: extract_all
def extract_all(snapshot_root: Path) -> tuple[SourceUserData, ...]:
    return tuple(extract_user(m, snapshot_root=snapshot_root) for m in USER_MAPPINGS)
