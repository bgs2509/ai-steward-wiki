# FILE: src/ai_steward_wiki/migration/report.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Render a Markdown migration_report.md summarising what the ETL
#            did (or would do, in --dry-run mode). Inputs are the TargetPlan
#            and optional LoadReport (post-execute counters). The report is
#            the single user-facing artefact for cutover review.
#   SCOPE: render_report.
#   DEPENDS: ai_steward_wiki.migration.{config,transform,load}
#   LINKS: M-MIGRATION-REPORT, aisw-0a5
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   render_report - TargetPlan (+ optional LoadReport) -> Markdown string
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-0a5 P5.1-P5.2: report renderer
# END_CHANGE_SUMMARY

from __future__ import annotations

from collections import Counter

from ai_steward_wiki.migration.config import USER_MAPPINGS
from ai_steward_wiki.migration.load import LoadReport
from ai_steward_wiki.migration.transform import TargetPlan

__all__ = ["render_report"]


def render_report(
    plan: TargetPlan,
    *,
    mode: str,  # "dry-run" | "execute"
    snapshot_root: str,
    snapshot_date: str,
    load_report: LoadReport | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Migration report — ai-steward → ai-steward-wiki\n")
    lines.append(f"- **Run mode:** `{mode}`\n")
    lines.append(f"- **Snapshot:** `{snapshot_root}` (date: {snapshot_date})\n")
    lines.append("- **Beads issue:** aisw-0a5\n\n")

    # Identity table
    lines.append("## Identity (users.toml)\n\n")
    lines.append("| telegram_id | display_name | role | source_dir |\n")
    lines.append("|-------------|--------------|------|------------|\n")
    for um in USER_MAPPINGS:
        lines.append(
            f"| `{um.telegram_id}` | {um.display_name} | {um.role} | `{um.source_dir}` |\n"
        )
    lines.append("\n")

    # Per-user plan summary
    lines.append("## Per-user breakdown\n\n")
    jobs_per_owner = Counter(pj.owner_telegram_id for pj in plan.jobs)
    files_per_owner = Counter(pf.owner_telegram_id for pf in plan.files)
    docs_per_owner = Counter(pd.owner_telegram_id for pd in plan.legacy_docs)
    wikis_per_owner: dict[int, list[str]] = {}
    for pw in plan.wikis:
        wikis_per_owner.setdefault(pw.owner_telegram_id, []).append(pw.raw_name)

    for um in USER_MAPPINGS:
        tid = um.telegram_id
        wikis = ", ".join(sorted(wikis_per_owner.get(tid, []))) or "—"
        lines.append(f"### {um.display_name} (`{tid}`)\n\n")
        lines.append(f"- WIKIs: {wikis}\n")
        lines.append(f"- Jobs: {jobs_per_owner.get(tid, 0)}\n")
        lines.append(f"- Files: {files_per_owner.get(tid, 0)}\n")
        lines.append(f"- Legacy docs: {docs_per_owner.get(tid, 0)}\n\n")

    # WIKI breakdown
    lines.append("## WIKIs to bootstrap\n\n")
    lines.append("| owner | wiki | template |\n")
    lines.append("|-------|------|----------|\n")
    for pw in plan.wikis:
        lines.append(f"| `{pw.owner_telegram_id}` | `{pw.raw_name}-WIKI` | `{pw.template_id}` |\n")
    lines.append("\n")

    # Job kind summary
    kinds = Counter(pj.kind for pj in plan.jobs)
    lines.append("## Jobs by kind\n\n")
    for kind, n in kinds.most_common():
        lines.append(f"- `{kind}`: {n}\n")
    lines.append(f"- **Total:** {len(plan.jobs)}\n\n")

    # Dropped items
    if plan.dropped:
        lines.append("## Dropped (with reasons)\n\n")
        reasons = Counter(reason for _, reason in plan.dropped)
        for reason, n in reasons.most_common():
            lines.append(f"- `{reason}`: {n}\n")
        lines.append("\n<details><summary>Per-item drop list</summary>\n\n")
        for path, reason in plan.dropped:
            lines.append(f"- `{path}` — {reason}\n")
        lines.append("\n</details>\n\n")

    # Warnings
    if plan.warnings:
        lines.append("## Warnings\n\n")
        for w in plan.warnings:
            lines.append(f"- {w}\n")
        lines.append("\n")

    # Execute-mode counters (if any)
    if load_report is not None:
        lines.append("## Apply phase counters\n\n")
        lines.append(f"- users.toml written: `{load_report.users_toml_written}`\n")
        lines.append(f"- profiles_dir created: `{load_report.profiles_dir_created}`\n")
        lines.append(f"- WIKIs created: {load_report.wikis_created}\n")
        lines.append(f"- WIKIs already existed (skipped): {load_report.wikis_already_existed}\n")
        lines.append(f"- Files copied: {load_report.files_copied}\n")
        lines.append(f"- Files skipped (same size): {load_report.files_skipped_existing}\n")
        lines.append(f"- Legacy docs written: {load_report.legacy_docs_written}\n")
        lines.append(f"- Jobs inserted: {load_report.jobs_inserted}\n")
        if load_report.db_snapshot_path:
            lines.append(f"- jobs.db snapshot: `{load_report.db_snapshot_path}`\n")
        if load_report.errors:
            lines.append("\n### Errors\n\n")
            for e in load_report.errors:
                lines.append(f"- {e}\n")
        lines.append("\n")

    # Cutover checklist
    lines.append("## Cutover checklist (manual)\n\n")
    lines.append("1. Stop old bot on vpn-0: `ssh vpn-0 'sudo systemctl stop ai-steward'`\n")
    lines.append(
        "2. Stop new bot on vpn-gpu-1: `ssh vpn-gpu-1 'pkill -f \"python -m ai_steward_wiki\"'`\n"
    )
    lines.append(
        "3. Re-rsync snapshot (in case data changed since dry-run): "
        "`rsync -aH --delete vpn-0:/home/bgs/ai-steward/ /tmp/migration-snapshot/`\n"
    )
    lines.append("4. Run `--execute` from this same script\n")
    lines.append(
        "5. Restart new bot: "
        "`ssh vpn-gpu-1 'cd /home/bgs/works/ai-steward-wiki && nohup uv run python -m ai_steward_wiki > /tmp/aisw.log 2>&1 &'`\n"
    )
    lines.append(
        "6. Sanity check: TG-id 763463467 receives a message; verify users.toml has 5 entries.\n"
    )
    lines.append("7. Announce to users.\n")

    return "".join(lines)
