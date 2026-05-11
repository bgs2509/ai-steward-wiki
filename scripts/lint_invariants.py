#!/usr/bin/env python3
"""INV-1..INV-14 lint — repo-wide invariant grep-checker.

Each check is independent and reports its own ok/violation line. The script
exits 1 if ANY check fails. Checks are derived from
docs/Spec-WIKI/research/tech-spec-draft.md §0 "Invariants" closed list.

Scope by check:
  - Code-level invariants (INV-3, INV-4, INV-6, INV-7, INV-11, INV-12, INV-13)
    run against src/ai_steward_wiki/.
  - Spec-doc invariants (INV-1, INV-2, INV-5, INV-8, INV-9, INV-10, INV-14)
    are advisory presence-checks against the tech-spec; missing markers warn
    but do not fail (spec is in life-zone and may legitimately move).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "ai_steward_wiki"
SPEC = REPO_ROOT / "docs" / "Spec-WIKI" / "research" / "tech-spec-draft.md"

Result = tuple[str, bool, str]  # (inv_id, ok, message)


# ---------- helpers ----------


def _iter_py(root: Path, allowlist: set[Path] | None = None) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    allow = allowlist or set()
    for py in root.rglob("*.py"):
        if py in allow:
            continue
        out.append((py, py.read_text(encoding="utf-8")))
    return out


def _grep_src(patterns: list[re.Pattern[str]], allowlist: set[Path] | None = None) -> list[str]:
    violations: list[str] = []
    for path, text in _iter_py(SRC, allowlist):
        for pat in patterns:
            for m in pat.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                violations.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {m.group(0)!r}")
    return violations


def _spec_has(pattern: str) -> bool:
    if not SPEC.exists():
        return False
    return re.search(pattern, SPEC.read_text(encoding="utf-8")) is not None


# ---------- checks ----------


def inv_1() -> Result:
    """D-006 schema coverage — spec-doc advisory: §10.4 retention table exists."""
    ok = _spec_has(r"INV-1\.\s+D-006 schema coverage")
    return (
        "INV-1",
        True,
        "ok (advisory: spec marker present)" if ok else "warn (spec marker missing)",
    )


def inv_2() -> Result:
    """Job kinds closed list — advisory."""
    ok = _spec_has(r"INV-2\.")
    return ("INV-2", True, "ok (advisory)" if ok else "warn (spec marker missing)")


def inv_3() -> Result:
    """No `git push` / remote configuration in wiki-git / backup code (D-037)."""
    targets = [SRC / "ops" / "wiki_git", SRC / "ops" / "backup"]
    violations: list[str] = []
    pat = re.compile(r"\bgit\s+push\b|set[_-]?remote|add[_-]?remote|remote\s+add\b")
    for tdir in targets:
        if not tdir.exists():
            continue
        for py, text in _iter_py(tdir):
            for m in pat.finditer(text):
                ln = text[: m.start()].count("\n") + 1
                violations.append(f"{py.relative_to(REPO_ROOT)}:{ln}: {m.group(0)!r}")
    if violations:
        return (
            "INV-3",
            False,
            "violation: git push / remote in backup-context\n  " + "\n  ".join(violations),
        )
    return ("INV-3", True, "ok (no `git push` / remote in wiki_git+backup)")


def inv_4() -> Result:
    """Idempotency layers live in audit.db, not jobs.db."""
    pat = [re.compile(r"jobs\.db\.(seen_files|tg_updates)\b")]
    v = _grep_src(pat)
    return (
        ("INV-4", False, "violation:\n  " + "\n  ".join(v))
        if v
        else ("INV-4", True, "ok (no jobs.db.seen_files / jobs.db.tg_updates refs)")
    )


def inv_5() -> Result:
    """Identity vocabulary — advisory."""
    ok = _spec_has(r"INV-5\.")
    return ("INV-5", True, "ok (advisory)" if ok else "warn (spec marker missing)")


def inv_6() -> Result:
    """API backend credential is separated — Settings validator MUST exist."""
    settings = SRC / "settings.py"
    if not settings.exists():
        return ("INV-6", False, "violation: src/ai_steward_wiki/settings.py missing")
    text = settings.read_text(encoding="utf-8")
    has_validator = bool(re.search(r"anthropic_api", text) and re.search(r"credential", text, re.I))
    return (
        ("INV-6", True, "ok (Settings validates anthropic_api credential separation)")
        if has_validator
        else ("INV-6", False, "violation: settings.py lacks anthropic_api credential validator")
    )


def inv_7() -> Result:
    """No destructive FS mutations on wiki paths outside wiki/lifecycle.py."""
    allow = {
        SRC / "wiki" / "lifecycle.py",
        SRC / "ops" / "retention.py",  # purges staged inbox + retention dirs, not WIKI dirs
        SRC / "ops" / "snapshot.py",  # purges expired snapshot dirs, not WIKI dirs
    }
    patterns = [
        re.compile(r"\bshutil\.rmtree\b"),
        re.compile(r"\bos\.rmdir\b"),
        re.compile(r"\brm\s+-rf\b"),
        re.compile(r"\bsubprocess\..*['\"]mv['\"]"),
    ]
    v = _grep_src(patterns, allow)
    return (
        ("INV-7", False, "violation:\n  " + "\n  ".join(v))
        if v
        else ("INV-7", True, "ok (no forbidden destructive FS calls outside wiki/lifecycle.py)")
    )


def inv_8() -> Result:
    """Tech-spec numbers reference D-NNN — advisory."""
    ok = _spec_has(r"INV-8\.")
    return ("INV-8", True, "ok (advisory)" if ok else "warn (spec marker missing)")


def inv_9() -> Result:
    """Audit/sessions tables → schema sketch — advisory."""
    ok = _spec_has(r"INV-9\.")
    return ("INV-9", True, "ok (advisory)" if ok else "warn (spec marker missing)")


def inv_10() -> Result:
    """Cross-store FK convention — no FK from jobs.db/audit.db → sessions.db.users."""
    pat = [
        re.compile(r"(jobs|audit)\.db\.[a-z_]+\.user_id\s*FK", re.I),
        re.compile(r"FOREIGN KEY\s*\(\s*user_id\s*\)\s*REFERENCES\s+users", re.I),
    ]
    v = _grep_src(pat)
    # additionally search SQL migrations
    for sql in (REPO_ROOT / "alembic").rglob("*.py"):
        text = sql.read_text(encoding="utf-8")
        for p in pat:
            for m in p.finditer(text):
                ln = text[: m.start()].count("\n") + 1
                v.append(f"{sql.relative_to(REPO_ROOT)}:{ln}: {m.group(0)!r}")
    return (
        ("INV-10", False, "violation:\n  " + "\n  ".join(v))
        if v
        else ("INV-10", True, "ok (no cross-DB FK on users)")
    )


def inv_11() -> Result:
    """DLQ table must be `jobs_dlq` in jobs.db."""
    models = SRC / "storage" / "jobs" / "models.py"
    if not models.exists():
        return ("INV-11", False, "violation: storage/jobs/models.py missing")
    ok = "jobs_dlq" in models.read_text(encoding="utf-8")
    return (
        ("INV-11", True, "ok (jobs_dlq table declared in jobs storage)")
        if ok
        else ("INV-11", False, "violation: jobs_dlq table not found in jobs/models.py")
    )


def inv_12() -> Result:
    """Timeout counts as failure-strike."""
    failure = SRC / "scheduler" / "failure.py"
    if not failure.exists():
        return ("INV-12", False, "violation: scheduler/failure.py missing")
    text = failure.read_text(encoding="utf-8")
    ok = "INV-12" in text and re.search(r"timeout", text, re.I) is not None
    return (
        ("INV-12", True, "ok (failure.py references INV-12 + timeout)")
        if ok
        else ("INV-12", False, "violation: failure.py missing INV-12/timeout reference")
    )


def inv_13() -> Result:
    """Prompt paths absolute — no bare `prompts/<file>.md` in src/."""
    pat = re.compile(r"['\"]prompts/[A-Za-z0-9_.\-]+\.md['\"]")
    violations: list[str] = []
    for py, text in _iter_py(SRC):
        for m in pat.finditer(text):
            ln = text[: m.start()].count("\n") + 1
            violations.append(f"{py.relative_to(REPO_ROOT)}:{ln}: {m.group(0)!r}")
    return (
        (
            "INV-13",
            False,
            "violation: bare prompts/ path (must be absolute / settings-driven):\n  "
            + "\n  ".join(violations),
        )
        if violations
        else ("INV-13", True, "ok (no bare prompts/ literals in src/)")
    )


def inv_14() -> Result:
    """Anti-spam section SSoT in §5 — advisory."""
    ok = _spec_has(r"INV-14\.")
    return ("INV-14", True, "ok (advisory)" if ok else "warn (spec marker missing)")


CHECKS: list[Callable[[], Result]] = [
    inv_1,
    inv_2,
    inv_3,
    inv_4,
    inv_5,
    inv_6,
    inv_7,
    inv_8,
    inv_9,
    inv_10,
    inv_11,
    inv_12,
    inv_13,
    inv_14,
]


def main() -> int:
    fail = 0
    for fn in CHECKS:
        inv_id, ok, msg = fn()
        prefix = f"{inv_id}:"
        if ok:
            print(f"{prefix} {msg}")
        else:
            sys.stderr.write(f"{prefix} {msg}\n")
            fail += 1
    if fail:
        sys.stderr.write(f"\n{fail} invariant check(s) failed\n")
        return 1
    print(f"\nAll {len(CHECKS)} invariant checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
