# FILE: src/ai_steward_wiki/migration/transform.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Pure transformation layer of ETL. SourceUserData -> TargetPlan.
#            All decision-making logic for: which planner items are active,
#            Moscow-to-UTC TZ conversion, category mapping, recipients fail-fast,
#            remind_before fan-out, monthly->cron_user workaround,
#            daily/weekly->cron_user with Recurrence, file routing to
#            <wiki>/<sub>/ vs <wiki>/raw/<sub>/, frontmatter rendering.
#            No FS or DB writes here — only in-memory PlannedJob /
#            PlannedFileCopy / PlannedLegacyDoc dataclasses.
#   SCOPE: PlannedJob, PlannedFileCopy, PlannedLegacyDoc, TargetPlan,
#          is_planner_active, msk_to_utc, map_category, extract_chat_id,
#          monthly_to_cron, daily_weekly_to_cron, build_recurrence,
#          planner_to_jobs, classify_file_target,
#          render_legacy_history_md, render_legacy_claude_md,
#          build_plan, build_plan_all.
#   DEPENDS: pydantic v2, ai_steward_wiki.migration.{config,extract},
#            ai_steward_wiki.classifier.recurrence, apscheduler.triggers.cron
#   LINKS: M-MIGRATION-TRANSFORM, aisw-0a5
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PlannedJob - one row that will be INSERTed into jobs.db
#   PlannedFileCopy - one (src, dst) file copy operation
#   PlannedLegacyDoc - one rendered Markdown file (legacy CLAUDE.md or planner-history)
#   PlannedWiki - one WIKI to bootstrap (owner, raw_name, template_id)
#   TargetPlan - composite of all the above, per-user grouped
#   is_planner_active - status filter + date/repeat filter
#   msk_to_utc - "YYYY-MM-DD HH:MM:SS" (MSK) -> aware UTC datetime
#   map_category - old category -> ReminderPayload.category enum (+ original)
#   extract_chat_id - recipients list[int] -> single int, fail-fast on len!=1
#   monthly_to_cron - {type:monthly, day, time} -> "M H D * *" (validated via APS)
#   daily_weekly_to_cron - {type:daily|weekly, ...} -> "M H * * *" or "M H * * <days>"
#   build_recurrence - Pydantic Recurrence for daily/weekly (sanity check)
#   planner_to_jobs - one SourcePlannerItem -> 0..N PlannedJob (fan-out)
#   classify_file_target - SourceFile -> target rel path inside <wiki>
#   render_legacy_history_md - inactive items -> Markdown string with frontmatter
#   render_legacy_claude_md - original CLAUDE.md content -> wrapped with frontmatter
#   build_plan - one SourceUserData -> per-user contribution to TargetPlan
#   build_plan_all - SourceUserData iterable + UserMapping iterable -> TargetPlan
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-0a5 P3.1-P3.8: pure transformation functions
# END_CHANGE_SUMMARY

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import structlog
from apscheduler.triggers.cron import CronTrigger

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.migration.config import (
    CATEGORY_MAP,
    PRIORITY_MAP,
    USER_MAPPINGS,
    ProjectMapping,
    UserMapping,
    find_project_mapping,
)
from ai_steward_wiki.migration.extract import (
    SourceFile,
    SourcePlannerItem,
    SourceUserData,
)

__all__ = [
    "PlannedFileCopy",
    "PlannedJob",
    "PlannedLegacyDoc",
    "PlannedWiki",
    "TargetPlan",
    "build_plan",
    "build_plan_all",
    "build_recurrence",
    "classify_file_target",
    "daily_weekly_to_cron",
    "extract_chat_id",
    "is_planner_active",
    "map_category",
    "monthly_to_cron",
    "msk_to_utc",
    "planner_to_jobs",
    "render_legacy_claude_md",
    "render_legacy_history_md",
]

_log = structlog.get_logger(__name__)

_MSK = ZoneInfo("Europe/Moscow")

_WEEKDAY_NAME_TO_IDX: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


# ============================================================
# Planned-* dataclasses (transform output)
# ============================================================


@dataclass(frozen=True, slots=True)
class PlannedJob:
    """One row to be INSERTed into jobs.jobs."""

    owner_telegram_id: int
    chat_id: int
    kind: Literal["reminder_job", "cron_user"]
    status: str
    priority: int
    scheduled_at_utc: datetime | None  # None for cron_user
    payload: dict[str, object]
    created_at_utc: datetime
    user_state: str = "pending"
    snooze_count: int = 0


@dataclass(frozen=True, slots=True)
class PlannedFileCopy:
    """One file-copy op: src -> abs_target. dry-run skips this."""

    src: Path
    target_rel: str  # relative to <wiki_root>/<owner>/<Wiki>-WIKI/
    owner_telegram_id: int
    wiki_raw_name: str


@dataclass(frozen=True, slots=True)
class PlannedLegacyDoc:
    """One generated Markdown file (legacy CLAUDE.md or planner-history.md)."""

    content: str
    target_rel: str  # relative to <wiki_root>/<owner>/<Wiki>-WIKI/
    owner_telegram_id: int
    wiki_raw_name: str


@dataclass(frozen=True, slots=True)
class PlannedWiki:
    """One WIKI to bootstrap via WikiLifecycleManager.create_wiki()."""

    owner_telegram_id: int
    raw_name: str  # e.g. "Medical"
    template_id: str


@dataclass(slots=True)
class TargetPlan:
    """Aggregate result of transform phase. Lists (not tuples) so we can append."""

    wikis: list[PlannedWiki] = field(default_factory=list)
    jobs: list[PlannedJob] = field(default_factory=list)
    files: list[PlannedFileCopy] = field(default_factory=list)
    legacy_docs: list[PlannedLegacyDoc] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    warnings: list[str] = field(default_factory=list)


# ============================================================
# Helpers
# ============================================================


# START_CONTRACT: is_planner_active
#   PURPOSE: Decide whether a legacy planner item should migrate to jobs.db.
#            Active = status='pending' AND (date >= now_msk OR repeat is set).
#   INPUTS: { item: SourcePlannerItem, now_msk_date: date }
#   OUTPUTS: { bool }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM, Q1.3.A
# END_CONTRACT: is_planner_active
def is_planner_active(item: SourcePlannerItem, now_msk_date: date) -> bool:
    if item.status != "pending":
        return False
    if item.repeat is not None:
        return True
    try:
        item_date = date.fromisoformat(item.date)
    except ValueError:
        return False
    return item_date >= now_msk_date


# START_CONTRACT: msk_to_utc
#   PURPOSE: Convert an "YYYY-MM-DD" + "HH:MM:SS" (MSK) pair to an aware UTC datetime.
#   INPUTS: { date_str: str, time_str: str | None - defaults to '00:00:00' }
#   OUTPUTS: { datetime - tz=UTC }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM, Q1.3.B
# END_CONTRACT: msk_to_utc
def msk_to_utc(date_str: str, time_str: str | None) -> datetime:
    ts = time_str or "00:00:00"
    naive = datetime.fromisoformat(f"{date_str}T{ts}")
    return naive.replace(tzinfo=_MSK).astimezone(UTC)


# START_CONTRACT: map_category
#   PURPOSE: Map legacy planner.category to ReminderPayload.category + preserve original.
#   INPUTS: { original: str }
#   OUTPUTS: { tuple[Literal['medication','event','generic'], str] - (new, original) }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM, Q1.3.E
# END_CONTRACT: map_category
def map_category(
    original: str,
) -> tuple[Literal["medication", "event", "generic"], str]:
    mapped = CATEGORY_MAP.get(original, "generic")
    return (mapped, original)


# START_CONTRACT: extract_chat_id
#   PURPOSE: Take a list of recipients and return the single chat_id.
#            Fail-fast (raise ValueError) on len != 1 — prescan verified
#            zero multi-recipient items, so this is defensive.
#   INPUTS: { recipients: list[int] }
#   OUTPUTS: { int }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM, Q1.3.C
# END_CONTRACT: extract_chat_id
def extract_chat_id(recipients: list[int]) -> int:
    if len(recipients) != 1:
        raise ValueError(
            f"recipients must have exactly 1 entry, got {len(recipients)}: {recipients}"
        )
    return recipients[0]


# START_BLOCK_CRON_BUILDERS
# START_CONTRACT: monthly_to_cron
#   PURPOSE: Convert legacy {type:monthly, day, time} -> APScheduler 5-field cron
#            "<MIN> <HOUR> <DAY> * *". Validated via CronTrigger.from_crontab.
#   INPUTS: { repeat: dict - must have 'day' (int) and 'time' (str 'HH:MM') }
#   OUTPUTS: { str - 5-field cron expression }
#   SIDE_EFFECTS: none (raises on invalid input)
#   LINKS: M-MIGRATION-TRANSFORM, Q1.3.D
# END_CONTRACT: monthly_to_cron
def monthly_to_cron(repeat: dict[str, object]) -> str:
    day = repeat.get("day")
    time_str = repeat.get("time")
    if not isinstance(day, int) or not isinstance(time_str, str):
        raise ValueError(f"invalid monthly repeat: {repeat!r}")
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid monthly time: {time_str!r}")
    hour, minute = int(parts[0]), int(parts[1])
    expr = f"{minute} {hour} {day} * *"
    # Validate by constructing CronTrigger — raises on invalid.
    CronTrigger.from_crontab(expr)
    return expr


# START_CONTRACT: daily_weekly_to_cron
#   PURPOSE: Convert legacy daily/weekly repeat to 5-field cron.
#   INPUTS: { repeat: dict - {type:'daily'|'weekly', time:'HH:MM', days?: list[str]} }
#   OUTPUTS: { str - 5-field cron expression }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM
# END_CONTRACT: daily_weekly_to_cron
def daily_weekly_to_cron(repeat: dict[str, object]) -> str:
    repeat_type = repeat.get("type")
    time_str = repeat.get("time")
    if not isinstance(time_str, str):
        raise ValueError(f"invalid time: {time_str!r}")
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid time: {time_str!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if repeat_type == "daily":
        expr = f"{minute} {hour} * * *"
    elif repeat_type == "weekly":
        days = repeat.get("days")
        if not isinstance(days, list) or not days:
            raise ValueError(f"weekly repeat needs non-empty 'days': {repeat!r}")
        # APScheduler accepts comma-separated 3-letter abbreviations.
        normalised = ",".join(d for d in days if d in _WEEKDAY_NAME_TO_IDX)
        if not normalised:
            raise ValueError(f"weekly repeat 'days' has no known values: {days!r}")
        expr = f"{minute} {hour} * * {normalised}"
    else:
        raise ValueError(f"unsupported repeat type: {repeat_type!r}")
    CronTrigger.from_crontab(expr)
    return expr


# END_BLOCK_CRON_BUILDERS


# START_CONTRACT: build_recurrence
#   PURPOSE: Build a Recurrence for daily/weekly. Sanity check that the
#            project's typed Recurrence still validates this shape.
#   INPUTS: { repeat: dict, user_tz: str }
#   OUTPUTS: { Recurrence }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM, M-CLASSIFIER-RECURRENCE
# END_CONTRACT: build_recurrence
def build_recurrence(repeat: dict[str, object], *, user_tz: str) -> Recurrence:
    repeat_type = repeat.get("type")
    time_str = repeat.get("time")
    if not isinstance(time_str, str):
        raise ValueError(f"invalid time: {time_str!r}")
    if repeat_type == "daily":
        return Recurrence(kind="daily", time_hhmm=time_str, weekdays=(), tz=user_tz)
    if repeat_type == "weekly":
        days = repeat.get("days")
        if not isinstance(days, list) or not days:
            raise ValueError(f"weekly repeat needs non-empty 'days': {repeat!r}")
        weekdays = tuple(_WEEKDAY_NAME_TO_IDX[d] for d in days if d in _WEEKDAY_NAME_TO_IDX)
        if not weekdays:
            raise ValueError(f"weekly repeat 'days' has no known values: {days!r}")
        return Recurrence(kind="weekly", time_hhmm=time_str, weekdays=weekdays, tz=user_tz)
    raise ValueError(f"unsupported repeat type for Recurrence: {repeat_type!r}")


# ============================================================
# Main: planner -> jobs
# ============================================================


def _wiki_id_for_item(
    item: SourcePlannerItem,
    *,
    user_mapping: UserMapping,
) -> tuple[str, str]:
    """Return (wiki_raw_name, template_id) for an item per Q3 mapping.

    Falls back to Default-WIKI if the item's project has no mapping (defence;
    config.py already covers every active project explicitly).
    """
    pm = find_project_mapping(user_mapping.telegram_id, item.project)
    if pm is None:
        # Should not happen for items that came from a configured project;
        # only the user-root planner items can hit this with project=None
        # and they're explicitly mapped to Default.
        return ("Default", "_default")
    return (pm.target_wiki, pm.template_id)


# START_CONTRACT: planner_to_jobs
#   PURPOSE: Turn one active SourcePlannerItem into a list of PlannedJob rows
#            with fan-out by remind_before. Monthly -> cron_user (cron_expr
#            workaround). Daily/weekly -> cron_user (with Recurrence sanity).
#            One-shot -> reminder_job per fan-out element. Category map +
#            payload.legacy_* keys for traceability.
#   INPUTS: { item: SourcePlannerItem, user_mapping: UserMapping, now_utc: datetime }
#   OUTPUTS: { list[PlannedJob] }
#   SIDE_EFFECTS: none (raises ValueError on bad data — Fail-Fast)
#   LINKS: M-MIGRATION-TRANSFORM, Q1.3.* decisions
# END_CONTRACT: planner_to_jobs
def planner_to_jobs(
    item: SourcePlannerItem,
    *,
    user_mapping: UserMapping,
    now_utc: datetime,
) -> list[PlannedJob]:
    chat_id = extract_chat_id(item.recipients)
    priority = PRIORITY_MAP.get(item.priority, 2)
    new_cat, legacy_cat = map_category(item.category)
    wiki_raw_name, _template_id = _wiki_id_for_item(item, user_mapping=user_mapping)

    legacy_payload_base: dict[str, object] = {
        "legacy_item_id": item.item_id,
        "legacy_category": legacy_cat,
    }

    out: list[PlannedJob] = []

    if item.repeat is not None:
        # Recurring -> single cron_user row (no fan-out, lead times don't
        # apply to cron triggers in the new model).
        repeat_type = item.repeat.get("type")
        if repeat_type == "monthly":
            cron_expr = monthly_to_cron(item.repeat)
            legacy_payload_base["legacy_source"] = "planner.json:monthly"
        elif repeat_type in {"daily", "weekly"}:
            # Sanity-check the Recurrence model still accepts it.
            build_recurrence(item.repeat, user_tz=user_mapping.tz)
            cron_expr = daily_weekly_to_cron(item.repeat)
            legacy_payload_base["legacy_source"] = f"planner.json:{repeat_type}"
        else:
            raise ValueError(f"unsupported repeat type for item {item.item_id}: {item.repeat!r}")

        user_text = item.title if not item.description else f"{item.title}\n\n{item.description}"
        payload: dict[str, object] = {
            "kind": "cron_user",
            "wiki_id": _wiki_id_marker(wiki_raw_name, user_mapping.telegram_id),
            "cron_expr": cron_expr,
            "user_text": user_text,
            **legacy_payload_base,
        }
        out.append(
            PlannedJob(
                owner_telegram_id=user_mapping.telegram_id,
                chat_id=chat_id,
                kind="cron_user",
                status="scheduled",
                priority=priority,
                scheduled_at_utc=None,
                payload=payload,
                created_at_utc=now_utc,
            )
        )
        return out

    # One-shot: fan-out across remind_before.
    base_utc = msk_to_utc(item.date, item.time_start)
    lead_times = item.remind_before or [0]
    message = item.title if not item.description else f"{item.title}\n\n{item.description}"

    for lead in lead_times:
        scheduled = base_utc - timedelta(minutes=lead)
        payload = {
            "kind": "reminder_job",
            "message": message,
            "lead_time_min": int(lead),
            "category": new_cat,
            **legacy_payload_base,
        }
        out.append(
            PlannedJob(
                owner_telegram_id=user_mapping.telegram_id,
                chat_id=chat_id,
                kind="reminder_job",
                status="scheduled",
                priority=priority,
                scheduled_at_utc=scheduled,
                payload=payload,
                created_at_utc=now_utc,
            )
        )

    return out


def _wiki_id_marker(wiki_raw_name: str, owner_telegram_id: int) -> str:
    """A stable identifier for the WIKI used in payloads.

    The new project's payloads use a `wiki_id` string. We don't have a
    formal id scheme exposed, so we store the canonical WIKI primary name
    (e.g. 'Medical-WIKI'). The runtime resolves it from the owner dir.
    """
    _ = owner_telegram_id  # owner context lives in Job.owner_telegram_id
    return f"{wiki_raw_name}-WIKI"


# ============================================================
# File classification -> target subpath
# ============================================================


# START_CONTRACT: classify_file_target
#   PURPOSE: Map a SourceFile to a relative target path inside its WIKI.
#            Returns None if file should be dropped at transform level.
#   INPUTS: { file: SourceFile }
#   OUTPUTS: { str | None - rel path inside <wiki>, or None means DROP }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM, Q1.4 + Q1.5 + Q1.6 decisions
# END_CONTRACT: classify_file_target
def classify_file_target(file: SourceFile) -> str | None:
    """Map a SourceFile to a relative target path inside its WIKI (None = DROP).

    Routing is purely location/file-type based; the owning ProjectMapping is
    resolved by the caller and is not needed here.
    """
    if file.file_type == "script":
        return None  # already filtered in extract, defence in depth

    # File name stays as-is at the leaf.
    leaf = file.abs_path.name

    if file.location == "data_subfolder":
        sub = file.subfolder or "_misc"
        if file.file_type in {"csv", "json", "md", "text"}:
            return f"{sub}/{leaf}"
        # PDF / image / binary -> raw/<sub>/
        return f"raw/{sub}/{leaf}"

    if file.location == "data_root":
        if file.file_type in {"csv", "json", "md", "text"}:
            return leaf
        return f"raw/{leaf}"

    if file.location == "output":
        return f"raw/legacy-output/{leaf}"

    if file.location in {"project_root", "user_root"}:
        return f"raw/legacy-root/{leaf}"

    # Unknown location
    _log.warning(
        "migration.transform.unknown_location",
        path=str(file.abs_path),
        location=file.location,
    )
    return f"raw/legacy-root/{leaf}"


# ============================================================
# Frontmatter rendering
# ============================================================


# START_CONTRACT: render_legacy_claude_md
#   PURPOSE: Wrap original CLAUDE.md content with frontmatter pointing at source.
#   INPUTS: { content: str, source_path: str, snapshot_date: str }
#   OUTPUTS: { str - Markdown with frontmatter prepended }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM, Q1.2 decision (raw-ingest)
# END_CONTRACT: render_legacy_claude_md
def render_legacy_claude_md(content: str, *, source_path: str, snapshot_date: str) -> str:
    fm = (
        "---\n"
        f"source: {source_path}\n"
        f"snapshot_date: {snapshot_date}\n"
        "migration_run_id: aisw-0a5\n"
        "kind: legacy-ai-steward-claude-md\n"
        "---\n\n"
    )
    return fm + content


# START_CONTRACT: render_legacy_history_md
#   PURPOSE: Render inactive planner items as a Markdown history page.
#   INPUTS: { items: list[SourcePlannerItem], source_path: str, snapshot_date: str }
#   OUTPUTS: { str - Markdown with frontmatter prepended }
#   SIDE_EFFECTS: none
#   LINKS: M-MIGRATION-TRANSFORM, Q1.3.A decision
# END_CONTRACT: render_legacy_history_md
def render_legacy_history_md(
    items: list[SourcePlannerItem], *, source_path: str, snapshot_date: str
) -> str:
    fm = (
        "---\n"
        f"source: {source_path}\n"
        f"snapshot_date: {snapshot_date}\n"
        "migration_run_id: aisw-0a5\n"
        "kind: legacy-planner-history\n"
        f"item_count: {len(items)}\n"
        "---\n\n"
        "# Legacy planner.json history\n\n"
        "Imported from old `ai-steward` bot. These items were inactive at "
        "migration time (completed, skipped, cancelled, or one-shot with "
        "past date) and are preserved here as raw history (Karpathy "
        "raw-source). LLM is free to promote relevant ones into entity "
        "pages during future query/ingest cycles.\n\n"
    )
    parts: list[str] = [fm]
    for it in items:
        parts.append(
            f"## {it.title}\n\n"
            f"- **Status:** {it.status}\n"
            f"- **Category:** {it.category}\n"
            f"- **Date:** {it.date}{' ' + it.time_start if it.time_start else ''}\n"
            f"- **Priority:** {it.priority}\n"
            f"- **ID:** {it.item_id}\n"
        )
        if it.description:
            parts.append(f"- **Description:** {it.description}\n")
        if it.repeat:
            parts.append(f"- **Repeat:** `{it.repeat}`\n")
        if it.sent_reminders:
            parts.append(f"- **Sent reminders:** {len(it.sent_reminders)}\n")
        parts.append("\n")
    return "".join(parts)


# ============================================================
# Top-level plan builder
# ============================================================


def _project_to_wiki_name_map(user: UserMapping) -> dict[str | None, ProjectMapping]:
    return {pm.source_project: pm for pm in user.projects}


# START_CONTRACT: build_plan
#   PURPOSE: One SourceUserData -> contribution to TargetPlan (mutates plan).
#            Builds: PlannedWiki entries (deduped), PlannedJob entries,
#            PlannedFileCopy entries, PlannedLegacyDoc entries (CLAUDE.md
#            and legacy-planner-history.md), drop+warning lists.
#   INPUTS: { data: SourceUserData, user_mapping: UserMapping,
#             now_utc: datetime, plan: TargetPlan, snapshot_date: str }
#   OUTPUTS: { None - mutates `plan` in place }
#   SIDE_EFFECTS: appends to plan
#   LINKS: M-MIGRATION-TRANSFORM
# END_CONTRACT: build_plan
def build_plan(
    data: SourceUserData,
    *,
    user_mapping: UserMapping,
    now_utc: datetime,
    plan: TargetPlan,
    snapshot_date: str,
) -> None:
    project_map = _project_to_wiki_name_map(user_mapping)

    # 1. WIKIs (dedup by raw_name within this owner)
    seen_wikis: set[str] = set()
    for pm in user_mapping.projects:
        if pm.target_wiki in seen_wikis:
            continue
        seen_wikis.add(pm.target_wiki)
        plan.wikis.append(
            PlannedWiki(
                owner_telegram_id=user_mapping.telegram_id,
                raw_name=pm.target_wiki,
                template_id=pm.template_id,
            )
        )

    # 2. Planner items -> jobs (active) / history doc (inactive)
    now_msk_date = now_utc.astimezone(_MSK).date()
    active_items: list[SourcePlannerItem] = []
    inactive_by_project: dict[str | None, list[SourcePlannerItem]] = {}
    for it in data.planner_items:
        if is_planner_active(it, now_msk_date):
            active_items.append(it)
        else:
            inactive_by_project.setdefault(it.project, []).append(it)

    for it in active_items:
        plan.jobs.extend(planner_to_jobs(it, user_mapping=user_mapping, now_utc=now_utc))

    # Legacy history -> per-project page in matching WIKI.
    for project, items in inactive_by_project.items():
        pm_hist = project_map.get(project)
        target_wiki: str
        if pm_hist is None:
            # Fallback to Default-WIKI history
            target_wiki = "Default"
            plan.warnings.append(
                f"history items for unmapped project {project!r} routed to Default-WIKI"
            )
        else:
            target_wiki = pm_hist.target_wiki
        source_path = (
            f"/home/bgs/ai-steward/{user_mapping.source_dir}"
            f"{'/' + project if project else ''}/planner.json"
        )
        plan.legacy_docs.append(
            PlannedLegacyDoc(
                content=render_legacy_history_md(
                    items, source_path=source_path, snapshot_date=snapshot_date
                ),
                target_rel="raw/legacy-planner-history.md",
                owner_telegram_id=user_mapping.telegram_id,
                wiki_raw_name=target_wiki,
            )
        )

    # 3. Files
    for f in data.files:
        pm_file: ProjectMapping | None = project_map.get(f.project)
        if pm_file is None:
            # user-root file (e.g. Marat root JPG) -> Default-WIKI
            pm_file = project_map.get(None)
        if pm_file is None:
            plan.dropped.append((str(f.abs_path), "no_target_wiki"))
            continue
        target_rel = classify_file_target(f)
        if target_rel is None:
            plan.dropped.append((str(f.abs_path), "transform_drop"))
            continue
        plan.files.append(
            PlannedFileCopy(
                src=f.abs_path,
                target_rel=target_rel,
                owner_telegram_id=user_mapping.telegram_id,
                wiki_raw_name=pm_file.target_wiki,
            )
        )

    # 4. CLAUDE.md files -> legacy-ai-steward-claude-md.md per WIKI
    for cmd in data.claude_md_files:
        # Determine project for this CLAUDE.md
        try:
            rel = cmd.relative_to(data.user_dir).parts
        except ValueError:
            continue
        project_for_cmd: str | None = rel[0] if len(rel) > 1 else None
        pm_cmd: ProjectMapping | None = (
            project_map.get(project_for_cmd) if project_for_cmd else project_map.get(None)
        )
        if pm_cmd is None:
            plan.dropped.append((str(cmd), "claude_md_no_target_wiki"))
            continue
        source_rel = "/".join(rel)
        source_path = f"/home/bgs/ai-steward/{user_mapping.source_dir}/{source_rel}"
        plan.legacy_docs.append(
            PlannedLegacyDoc(
                content=render_legacy_claude_md(
                    cmd.read_text(encoding="utf-8"),
                    source_path=source_path,
                    snapshot_date=snapshot_date,
                ),
                target_rel="raw/legacy-ai-steward-claude-md.md",
                owner_telegram_id=user_mapping.telegram_id,
                wiki_raw_name=pm_cmd.target_wiki,
            )
        )


# START_CONTRACT: build_plan_all
#   PURPOSE: Compose build_plan over all USER_MAPPINGS.
#   INPUTS: { source_data: tuple[SourceUserData, ...], now_utc: datetime, snapshot_date: str }
#   OUTPUTS: { TargetPlan }
#   SIDE_EFFECTS: none (only IO is read of CLAUDE.md files in build_plan)
#   LINKS: M-MIGRATION-TRANSFORM
# END_CONTRACT: build_plan_all
def build_plan_all(
    source_data: tuple[SourceUserData, ...],
    *,
    now_utc: datetime,
    snapshot_date: str,
) -> TargetPlan:
    plan = TargetPlan()
    by_tid = {d.telegram_id: d for d in source_data}
    for mapping in USER_MAPPINGS:
        data = by_tid.get(mapping.telegram_id)
        if data is None:
            plan.warnings.append(
                f"no source data for telegram_id={mapping.telegram_id} "
                f"({mapping.display_name}) — empty user_dir"
            )
            continue
        build_plan(
            data,
            user_mapping=mapping,
            now_utc=now_utc,
            plan=plan,
            snapshot_date=snapshot_date,
        )
    return plan
