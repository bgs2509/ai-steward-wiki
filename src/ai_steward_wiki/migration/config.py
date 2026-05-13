# FILE: src/ai_steward_wiki/migration/config.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Hardcoded mapping table from legacy {User}/{Project}/ tree to
#            new <wiki_root>/<owner>/<Name>-WIKI/ layout. Single SSoT for
#            identity + domain mapping (Q2/Q3 decisions, see Discovery).
#   SCOPE: UserMapping, ProjectMapping, USER_MAPPINGS, DROP_DIRS,
#          DROP_FILE_PATTERNS, CATEGORY_MAP, PRIORITY_MAP, lookup helpers.
#   DEPENDS: stdlib only.
#   LINKS: M-MIGRATION-CONFIG, aisw-0a5, D-042 (identity vocabulary)
#   ROLE: CONFIG
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   UserMapping - frozen dataclass: telegram_id, source_dir, display_name, role, tz, lang, projects
#   ProjectMapping - frozen dataclass: source_project (or None for root), target_wiki, template_id
#   USER_MAPPINGS - canonical tuple of 5 UserMapping entries
#   DROP_DIRS - frozenset of dir names that must never be migrated (dev/skeleton)
#   DROP_FILE_PATTERNS - file glob patterns dropped during walk
#   CATEGORY_MAP - old planner.category -> new ReminderPayload.category
#   PRIORITY_MAP - old planner.priority -> new Job.priority int
#   find_user_mapping - lookup by telegram_id
#   find_project_mapping - lookup project within a user
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-0a5 P1.4: initial mapping table per Q2/Q3 decisions
# END_CHANGE_SUMMARY

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "CATEGORY_MAP",
    "DROP_DIRS",
    "DROP_FILE_PATTERNS",
    "PRIORITY_MAP",
    "USER_MAPPINGS",
    "ProjectMapping",
    "UserMapping",
    "find_project_mapping",
    "find_user_mapping",
]


# START_BLOCK_DATACLASSES
@dataclass(frozen=True, slots=True)
class ProjectMapping:
    """One legacy project -> one target WIKI. source_project=None means
    the user-root level (e.g. /home/bgs/ai-steward/Gena_Beeline_VPN-0/planner.json)."""

    source_project: str | None
    target_wiki: str  # raw name pre-normalize, e.g. "Medical", "Career"
    template_id: str  # "medical", "budget", "investment", "career", "_default"


@dataclass(frozen=True, slots=True)
class UserMapping:
    """One legacy user folder -> one telegram_id with N project -> WIKI mappings."""

    telegram_id: int
    source_dir: Path  # relative to snapshot root
    display_name: str
    role: Literal["admin", "user"]
    projects: tuple[ProjectMapping, ...]
    tz: str = "Europe/Moscow"
    lang: str = "ru"


# END_BLOCK_DATACLASSES


# START_BLOCK_USER_MAPPINGS
# Q2/Q3 decisions: 5 users, 6 distinct telegram_ids (Gena + Gena_MTS as
# separate accounts of same physical person — Variant B from /q-a session).
USER_MAPPINGS: tuple[UserMapping, ...] = (
    UserMapping(
        telegram_id=763463467,
        source_dir=Path("Gena_Beeline_VPN-0"),
        display_name="Геннадий",
        role="admin",
        projects=(
            ProjectMapping("Health", "Medical", "medical"),
            ProjectMapping("Expenses", "Budget", "budget"),
            ProjectMapping("investment", "Investment", "investment"),
            ProjectMapping("2025_Noveo", "Career", "career"),
            ProjectMapping("NOVEO-ORANGE", "Career", "career"),
            ProjectMapping(None, "Default", "_default"),  # root planner.json
        ),
    ),
    UserMapping(
        telegram_id=6156629438,
        source_dir=Path("Gena_MTS"),
        display_name="Геннадий (MTS)",
        role="user",
        projects=(ProjectMapping("Noveo", "Career", "career"),),
    ),
    UserMapping(
        telegram_id=1151678530,
        source_dir=Path("Tania"),
        display_name="Татьяна",
        role="user",
        projects=(
            ProjectMapping("Health", "Medical", "medical"),
            ProjectMapping("Weightwatch", "Weightwatch", "_default"),
        ),
    ),
    UserMapping(
        telegram_id=1122408606,
        source_dir=Path("Dari"),
        display_name="Дари",
        role="user",
        projects=(),  # no content; users.toml entry only
    ),
    UserMapping(
        telegram_id=8587590035,
        source_dir=Path("Marat"),
        display_name="Марат",
        role="user",
        projects=(ProjectMapping(None, "Default", "_default"),),
    ),
)
# END_BLOCK_USER_MAPPINGS


# START_BLOCK_DROP_RULES
# Dev-code and skeleton dirs that must never be migrated (Q1.7).
DROP_DIRS: frozenset[str] = frozenset(
    {
        "Sensedar",
        "python-ai-skills",
        "Claude-bot",
        "prognosis",
        "test_fastapi",
        "scripts",
        "Fashionista",
        "ratelimit",
    }
)

# File glob patterns dropped during walk (Q1.4 + Q1.5 + Q8.B).
DROP_FILE_PATTERNS: tuple[str, ...] = (
    "*.py",  # any python script
    "*.bak",
    "notify.json",
    "notify.json.bak",
)
# END_BLOCK_DROP_RULES


# START_BLOCK_VALUE_MAPS
# Q1.3.E — old planner.category -> new ReminderPayload.category enum.
# Unknown values fall back to "generic" with original stored in payload.legacy_category.
CATEGORY_MAP: dict[str, Literal["medication", "event", "generic"]] = {
    "medication": "medication",
    "event": "event",
    "task": "generic",
    "reminder": "generic",
    "todo": "generic",
    "block": "generic",
}

# Old priority strings -> new Job.priority int (1..4, higher = more urgent).
PRIORITY_MAP: dict[str, int] = {
    "low": 1,
    "none": 2,
    "medium": 3,
    "high": 4,
}
# END_BLOCK_VALUE_MAPS


# START_CONTRACT: find_user_mapping
#   PURPOSE: Look up the UserMapping for a given telegram_id.
#   INPUTS: { telegram_id: int }
#   OUTPUTS: { UserMapping | None - the mapping, or None if unknown }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-CONFIG
# END_CONTRACT: find_user_mapping
def find_user_mapping(telegram_id: int) -> UserMapping | None:
    for um in USER_MAPPINGS:
        if um.telegram_id == telegram_id:
            return um
    return None


# START_CONTRACT: find_project_mapping
#   PURPOSE: Look up a ProjectMapping for (telegram_id, source_project_name).
#   INPUTS: { telegram_id: int, source_project: str | None }
#   OUTPUTS: { ProjectMapping | None - the mapping, or None if unknown }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-CONFIG
# END_CONTRACT: find_project_mapping
def find_project_mapping(telegram_id: int, source_project: str | None) -> ProjectMapping | None:
    user = find_user_mapping(telegram_id)
    if user is None:
        return None
    for pm in user.projects:
        if pm.source_project == source_project:
            return pm
    return None
