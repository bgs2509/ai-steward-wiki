#!/usr/bin/env python3
# FILE: scripts/classifier_regress.py
# START_MODULE_CONTRACT
#   PURPOSE: 100-case labelled-corpus regression harness gating every
#            prompts/classifier.md change against the REAL ClaudeCliBackend
#            (DEC-13, FR-13, aisw-xi8).
#   SCOPE: Load tests/corpus/classifier/questions.json, classify every case
#          through the real Stage-0 backend with bounded concurrency +
#          harness-only timeout retries, score intent/action/kind accuracy
#          plus the FR-12 verbatim-slot invariant, render a per-cluster
#          report, gate (intent 100%, intent+action+kind >=99%), and stamp
#          a sha256 of the gated prompt on PASS for the pre-commit guard.
#   DEPENDS: ai_steward_wiki.classifier.backend.ClaudeCliBackend,
#            ai_steward_wiki.classifier.schema (ClassifierError, ClassifierTimeoutError),
#            ai_steward_wiki.claude_cli.common.default_claude_config_dir
#   LINKS: M-CLASSIFIER-REGRESS, M-CLASSIFIER-STAGE0, DEC-13, FR-12, FR-13, aisw-xi8
#   ROLE: SCRIPT
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CorpusCase - one labelled corpus row (id, text, expected, accept)
#   Verdict - per-case scoring result (actual intent/action/kind, intent_ok/full_ok/verbatim_ok, error)
#   load_corpus - parse tests/corpus/classifier/questions.json into CorpusCase rows
#   score_verdict - score one classified case's distilled_payload against expected/accept + FR-12 verbatim invariant
#   run_regression - async: classify every corpus case via ClaudeCliBackend (semaphore=CONCURRENCY), collect Verdicts
#   render_report - render the per-cluster breakdown + misses/violations, decide gate pass/fail
#   write_stamp - record sha256(prompts/classifier.md) to .classifier_regress.stamp on GATE PASS
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 - full-audit 2026-07-08: the "mandatory manual gate"
#                had no enforcement (not in total-test, no pre-commit guard) —
#                add write_stamp(PROMPT_PATH) on GATE PASS; paired pre-commit
#                hook (scripts/check_classifier_regress_stamp.py) blocks
#                commits touching prompts/classifier.md unless the stamp's
#                sha256 matches the staged content.
#   PREVIOUS:    v0.1.0 - aisw-xi8 (e6ac1ad, DEC-13/FR-13): initial 100-case
#                classifier v2 regression harness against the real
#                ClaudeCliBackend; gates every prompts/classifier.md change,
#                deliberately excluded from make total-test/CI (100 real Haiku
#                calls per run). Step-13 grace-refresh added this contract
#                header (script previously had none) and fixed
#                docs/knowledge-graph.xml's M-CLASSIFIER-REGRESS node, which
#                referenced nonexistent fn-run_corpus/fn-score_verdicts and
#                was still STATUS="planned" despite being shipped and gated
#                green (make classifier-regress 100/100).
# END_CHANGE_SUMMARY
#
"""Classifier v2 regression harness (aisw-xi8, DEC-13, FR-13).

Runs tests/corpus/classifier/questions.json against the REAL ClaudeCliBackend
(prompts/classifier.md) with bounded concurrency, and reports a per-cluster
accuracy breakdown. MANDATORY MANUAL gate before any prompts/classifier.md
commit (documented in that file's own CHANGELOG discipline) — deliberately NOT
wired into `make total-test` / CI (100 real Haiku calls per run).

Usage: uv run python scripts/classifier_regress.py
Exit code: 0 on gate pass, 1 on gate fail.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_steward_wiki.classifier.backend import ClaudeCliBackend
from ai_steward_wiki.classifier.schema import ClassifierError, ClassifierTimeoutError
from ai_steward_wiki.claude_cli.common import default_claude_config_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = REPO_ROOT / "tests" / "corpus" / "classifier" / "questions.json"
PROMPT_PATH = REPO_ROOT / "prompts" / "classifier.md"
STAMP_PATH = REPO_ROOT / ".classifier_regress.stamp"


def write_stamp(prompt_path: Path, stamp_path: Path) -> None:
    """Record the sha256 of the gated prompt so the pre-commit guard can verify freshness."""
    digest = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    stamp_path.write_text(f"{digest}\n", encoding="utf-8")


CONCURRENCY = 5  # aisw-xi8: lowering this to 2 was tried and did NOT reduce the
# 30s-timeout rate (verified: two live runs, ~10-12/100 timeouts either way) —
# the timeouts are inherent per-call CLI/network latency variance, not local
# subprocess contention, so concurrency is reverted to 5 and TIMEOUT_RETRIES
# below is the actual fix for that noise.
TIMEOUT_RETRIES = 2  # aisw-xi8: up to 3 total attempts per case on
# ClassifierTimeoutError ONLY. This intentionally diverges from production
# classify()'s own policy (stage0.py: a bare ClassifierError gets one retry,
# aisw-l3h, but a ClassifierTimeoutError never retries — "retrying a 30s
# timeout would double the user's wait", aisw-32p) because this harness tests a
# DIFFERENT thing: prompts/classifier.md's content quality via the real
# ClaudeCliBackend, independent of transient CLI/network latency. Production's
# actual timeout-fallback behavior (degrade to intent=unknown) is already
# covered by its own dedicated tests in test_stage0.py (Task A2) — retrying
# here isolates the prompt-quality signal this harness exists to measure.
INTENT_GATE = 1.0  # 100% — FR-13
FULL_GATE = 0.99  # intent+action+kind >= 99% — FR-13
VERBATIM_SLOTS = ("time_expr", "schedule_expr", "text", "needle")  # FR-12

__all__ = [
    "CorpusCase",
    "Verdict",
    "load_corpus",
    "render_report",
    "run_regression",
    "score_verdict",
    "write_stamp",
]


@dataclass(frozen=True)
class CorpusCase:
    id: int
    text: str
    expected: dict[str, Any]
    accept: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Verdict:
    case: CorpusCase
    actual_intent: str | None
    actual_action: str | None
    actual_kind: str | None
    intent_ok: bool
    full_ok: bool
    verbatim_ok: bool
    error: str | None = None


def load_corpus(path: Path) -> list[CorpusCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        CorpusCase(
            id=row["id"],
            text=row["text"],
            expected=row["expected"],
            accept=tuple(row.get("accept", ())),
        )
        for row in raw
    ]


def _matches(
    candidate: dict[str, Any], intent: str | None, action: str | None, kind: str | None
) -> bool:
    if candidate.get("intent") != intent:
        return False
    if "action" in candidate and candidate["action"] != action:
        return False
    return not ("kind" in candidate and candidate["kind"] != kind)


def score_verdict(
    case: CorpusCase,
    *,
    intent: str | None,
    action: str | None,
    kind: str | None,
    distilled_payload: dict[str, Any],
    error: str | None = None,
) -> Verdict:
    """Score one classified case against its expected/accept candidates.

    A backend error scores as a full miss on every axis (never silently
    dropped from the denominator). verbatim_ok is FR-12's regression
    invariant: every free-text slot present must be a case-insensitive
    substring of the ORIGINAL message text — a violation means the model
    translated/paraphrased instead of copying verbatim.
    """
    if error is not None:
        return Verdict(case, None, None, None, False, False, False, error=error)
    candidates = (case.expected, *case.accept)
    intent_ok = any(c.get("intent") == intent for c in candidates)
    full_ok = any(_matches(c, intent, action, kind) for c in candidates)
    verbatim_ok = True
    for slot in VERBATIM_SLOTS:
        val = distilled_payload.get(slot)
        if isinstance(val, str) and val.strip() and val.casefold() not in case.text.casefold():
            verbatim_ok = False
    return Verdict(case, intent, action, kind, intent_ok, full_ok, verbatim_ok)


async def _classify_one(
    backend: ClaudeCliBackend, case: CorpusCase, sem: asyncio.Semaphore
) -> Verdict:
    async with sem:
        raw: dict[str, Any] | None = None
        last_error: str | None = None
        for attempt in range(TIMEOUT_RETRIES + 1):
            try:
                raw = await backend.call(
                    text=case.text,
                    prompt_path=PROMPT_PATH,
                    correlation_id=f"regress-{case.id}-a{attempt}",
                )
                break
            except ClassifierTimeoutError as exc:
                last_error = str(exc)
                continue  # aisw-xi8: harness-only retry, see TIMEOUT_RETRIES docstring
            except ClassifierError as exc:
                return score_verdict(
                    case, intent=None, action=None, kind=None, distilled_payload={}, error=str(exc)
                )
        if raw is None:
            return score_verdict(
                case,
                intent=None,
                action=None,
                kind=None,
                distilled_payload={},
                error=last_error,
            )
        payload = raw.get("distilled_payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        return score_verdict(
            case,
            intent=raw.get("intent"),
            action=payload.get("action"),
            kind=payload.get("kind"),
            distilled_payload=payload,
        )


async def run_regression(cases: list[CorpusCase], backend: ClaudeCliBackend) -> list[Verdict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    return list(await asyncio.gather(*(_classify_one(backend, c, sem) for c in cases)))


def render_report(verdicts: list[Verdict]) -> tuple[str, bool]:
    """Render the per-cluster breakdown and decide the gate. Returns (report, passed)."""
    total = len(verdicts)
    n_intent_ok = sum(v.intent_ok for v in verdicts)
    n_full_ok = sum(v.full_ok for v in verdicts)
    n_verbatim_ok = sum(v.verbatim_ok for v in verdicts)
    intent_acc = n_intent_ok / total if total else 0.0
    full_acc = n_full_ok / total if total else 0.0
    verbatim_acc = n_verbatim_ok / total if total else 0.0

    by_intent: dict[str, list[Verdict]] = defaultdict(list)
    for v in verdicts:
        by_intent[v.case.expected.get("intent", "?")].append(v)

    lines = [
        "=== classifier_regress report ===",
        f"total cases: {total}",
        f"intent accuracy:            {n_intent_ok}/{total} ({intent_acc:.1%})  gate>={INTENT_GATE:.0%}",
        f"intent+action+kind accuracy: {n_full_ok}/{total} ({full_acc:.1%})  gate>={FULL_GATE:.0%}",
        f"verbatim-slot invariant:     {n_verbatim_ok}/{total} ({verbatim_acc:.1%})",
        "",
        "-- per-cluster (expected.intent) breakdown --",
    ]
    for cluster in sorted(by_intent):
        cverds = by_intent[cluster]
        c_intent_ok = sum(v.intent_ok for v in cverds)
        c_full_ok = sum(v.full_ok for v in cverds)
        lines.append(
            f"  {cluster:10s} intent {c_intent_ok}/{len(cverds)}  full {c_full_ok}/{len(cverds)}"
        )
    misses = [v for v in verdicts if not v.full_ok]
    if misses:
        lines.append("")
        lines.append("-- misses (full: intent+action+kind) --")
        for v in misses:
            reason = v.error or (
                "intent" if not v.intent_ok else "action/kind" if not v.full_ok else "?"
            )
            lines.append(
                f"  #{v.case.id:3d} text={v.case.text!r} expected={v.case.expected} "
                f"actual=(intent={v.actual_intent}, action={v.actual_action}, kind={v.actual_kind}) "
                f"reason={reason}"
            )
    non_verbatim = [v for v in verdicts if not v.verbatim_ok]
    if non_verbatim:
        lines.append("")
        lines.append("-- verbatim-slot violations (FR-12) --")
        for v in non_verbatim:
            lines.append(f"  #{v.case.id:3d} text={v.case.text!r}")

    passed = intent_acc >= INTENT_GATE and full_acc >= FULL_GATE
    lines.append("")
    lines.append(f"GATE: {'PASS' if passed else 'FAIL'}")
    return "\n".join(lines), passed


async def _amain() -> int:
    cases = load_corpus(CORPUS_PATH)
    # aisw-xi8: mirror the production Stage-0 config — thinking disabled
    # (max_thinking_tokens=0, same as _build_classifier_backend in __main__.py).
    # The harness must measure the prompt exactly as production runs it.
    backend = ClaudeCliBackend(
        claude_config_dir=default_claude_config_dir(),
        max_thinking_tokens=0,
    )
    verdicts = await run_regression(cases, backend)
    report, passed = render_report(verdicts)
    print(report)
    if passed:
        write_stamp(PROMPT_PATH, STAMP_PATH)
    return 0 if passed else 1


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
