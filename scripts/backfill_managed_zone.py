#!/usr/bin/env python3
"""aisw-db6 backfill: re-render the managed zone of existing per-WIKI CLAUDE.md.

create_wiki historically wrote a frontmatter-only CLAUDE.md (empty managed zone),
so the model never received the Data layout schema. This one-shot, idempotent
script walks every <owner>/<WIKI>/CLAUDE.md under the wiki root, reads its
template_id, and re-renders the managed zone from templates/<template_id>.md via
migration.repair_managed_zone — preserving each WIKI's user zone verbatim.

Safe to re-run: a WIKI whose managed zone already matches its template (same sha)
is a no-op. Trash directories (`_trash`) are skipped.

Usage:
    uv run python scripts/backfill_managed_zone.py \
        --wiki-root /home/bgs/.local/share/ai-steward-wiki/workspace/wikis \
        --templates-dir /home/bgs/works/ai-steward-wiki/templates
    # add --dry-run to report without writing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_steward_wiki.wiki.migration import (
    FrontmatterError,
    TemplateNotFoundError,
    load_template,
    parse_frontmatter,
    repair_managed_zone,
)

_TRASH_DIR = "_trash"


def _iter_claude_md(wiki_root: Path):
    """Yield every per-WIKI CLAUDE.md path: <owner>/<WIKI>/CLAUDE.md."""
    for owner_dir in sorted(wiki_root.iterdir()):
        if not owner_dir.is_dir():
            continue
        for wiki_dir in sorted(owner_dir.iterdir()):
            if not wiki_dir.is_dir() or wiki_dir.name == _TRASH_DIR:
                continue
            claude = wiki_dir / "CLAUDE.md"
            if claude.is_file():
                yield claude


def backfill(wiki_root: Path, templates_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Repair managed zones for every WIKI under wiki_root. Returns a counts dict.

    Idempotent and side-effect-free under dry_run. Testable entrypoint behind main().
    """
    counts = {"fixed": 0, "noop": 0, "skipped": 0, "errors": 0}
    for claude in _iter_claude_md(wiki_root):
        try:
            fm, _ = parse_frontmatter(claude.read_text(encoding="utf-8"))
        except FrontmatterError as exc:
            print(f"ERROR {claude}: bad frontmatter: {exc}")
            counts["errors"] += 1
            continue
        try:
            managed, sha = load_template(fm.template_id, templates_dir)
        except TemplateNotFoundError:
            print(f"SKIP  {claude}: template_id={fm.template_id!r} not found")
            counts["skipped"] += 1
            continue

        if dry_run:
            needs = fm.template_sha256 != sha
            print(f"{'WOULD-FIX' if needs else 'OK'} {claude} (template={fm.template_id})")
            counts["fixed" if needs else "noop"] += 1
            continue

        changed = repair_managed_zone(
            claude, template_managed=managed, template_sha256=sha, template_id=fm.template_id
        )
        print(f"{'FIXED' if changed else 'NOOP '} {claude} (template={fm.template_id})")
        counts["fixed" if changed else "noop"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill v2 CLAUDE.md managed zones.")
    ap.add_argument("--wiki-root", type=Path, required=True)
    ap.add_argument("--templates-dir", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    counts = backfill(args.wiki_root, args.templates_dir, dry_run=args.dry_run)
    print(
        f"\nDone. fixed={counts['fixed']} noop={counts['noop']} "
        f"skipped={counts['skipped']} errors={counts['errors']} (dry_run={args.dry_run})"
    )
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
