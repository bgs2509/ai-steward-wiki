#!/usr/bin/env python3
# FILE: scripts/classifier_eval.py
# START_MODULE_CONTRACT
#   PURPOSE: OFFLINE eval harness for the Stage-0 classifier prompt quality (aisw-32p, aisw-zgf).
#   SCOPE: Feed a labelled corpus through the REAL Stage-0 classifier (worktree code +
#          local authenticated `claude` CLI / Haiku) and print precision/recall, accuracy,
#          unknown-rate, admin false-positives, list-wikis routing check, and a miss list.
#   DEPENDS: ai_steward_wiki.classifier (classify, ClaudeCliBackend, PromptCache, Intent),
#            ai_steward_wiki.claude_cli.common.default_claude_config_dir
#   LINKS: M-CLASSIFIER-STAGE0, docs/superpowers/specs/20260626-classifier-quality-design.md
#   ROLE: SCRIPT
#   MAP_MODE: LOCALS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Case - one labelled corpus row (text, expected, forbid, list_route, note)
#   Outcome - per-message eval result (predicted intent, confidence, error)
#   load_corpus - parse the JSONL corpus into Case rows
#   classify_one - run one message through the REAL Stage-0 classifier
#   report - print metrics (P/R, accuracy, unknown-rate, admin-FP, list-route check, misses)
#   main - CLI entrypoint; fan out classifications and print the report
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-32p/aisw-zgf: initial offline Stage-0 eval harness.
# END_CHANGE_SUMMARY
#
# WARNING — NOT part of `make total-test` / CI:
#   * Makes REAL, PAID, NON-DETERMINISTIC Haiku calls (one `claude` CLI subprocess per
#     corpus message).
#   * Requires an authenticated `claude` CLI (subscription auth) + network.
#   total-test must stay deterministic and offline, so this harness is a MANUAL quality gate
#   run by a human before/after a prompt change. The deterministic timeout-fallback behaviour
#   is covered separately by a normal unit test (tests/unit/classifier/test_stage0.py).
#
# Usage:
#   uv run python scripts/classifier_eval.py
#   uv run python scripts/classifier_eval.py --prompt prompts/classifier.md \
#       --corpus tests/fixtures/classifier_corpus.jsonl --concurrency 4

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Allow running from a checkout without an editable install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ai_steward_wiki.classifier import (  # noqa: E402
    ClassifierError,
    ClaudeCliBackend,
    PromptCache,
    classify,
)
from ai_steward_wiki.claude_cli.common import default_claude_config_dir  # noqa: E402


@dataclass(frozen=True)
class Case:
    text: str
    expected: str
    forbid: str | None
    list_route: bool
    note: str


@dataclass
class Outcome:
    case: Case
    predicted: str | None
    confidence: float | None
    error: str | None


def load_corpus(path: Path) -> list[Case]:
    cases: list[Case] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        cases.append(
            Case(
                text=obj["text"],
                expected=obj["expected_intent"],
                forbid=obj.get("forbid_intent"),
                list_route=bool(obj.get("list_route", False)),
                note=obj.get("note", ""),
            )
        )
    return cases


# Stage-0 intents that the tg pipeline forwards to the Inbox router (Stage-1a), which owns
# the list_wikis path. Mirror of _ROUTABLE_INTENTS in src/ai_steward_wiki/tg/pipeline.py.
_ROUTABLE = {"wiki_ingest", "unknown"}


async def classify_one(
    case: Case,
    *,
    backend: ClaudeCliBackend,
    prompt_path: Path,
    cache: PromptCache,
    sem: asyncio.Semaphore,
    idx: int,
) -> Outcome:
    async with sem:
        try:
            res = await classify(
                case.text,
                correlation_id=f"eval-{idx}",
                backend=backend,
                prompt_path=prompt_path,
                cache=cache,
            )
            return Outcome(case, res.intent.value, res.confidence, None)
        except ClassifierError as e:
            # Report the failure for this message; do not crash the whole run.
            return Outcome(case, None, None, f"{type(e).__name__}: {e}")


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):5.1f}%" if d else "  n/a"


def report(outcomes: list[Outcome]) -> bool:
    n = len(outcomes)
    correct = 0
    unknown_pred = 0
    admin_fp = 0
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    labels = sorted({o.case.expected for o in outcomes} | {"unknown", "admin"})
    misses: list[Outcome] = []
    list_route_fail: list[Outcome] = []

    for o in outcomes:
        pred = o.predicted
        exp = o.case.expected
        if pred == "unknown":
            unknown_pred += 1
        if pred == "admin" and exp != "admin":
            admin_fp += 1
        if o.case.list_route and (pred == "admin" or pred not in _ROUTABLE):
            list_route_fail.append(o)
        if pred == exp:
            correct += 1
            tp[exp] += 1
        else:
            if pred is not None:
                fp[pred] += 1
            fn[exp] += 1
            misses.append(o)

    print("=" * 72)
    print(f"CORPUS: {n} messages")
    print("=" * 72)
    print(f"overall accuracy      : {_pct(correct, n)}  ({correct}/{n})")
    print(f"unknown-rate (pred)   : {_pct(unknown_pred, n)}  ({unknown_pred}/{n})")
    print(f"admin false-positives : {admin_fp}  (expected!=admin but predicted==admin)")
    n_errors = sum(1 for o in outcomes if o.error is not None)
    print(f"backend errors        : {n_errors}")
    print()
    print("per-intent  precision / recall  (support)")
    print("-" * 72)
    for lbl in labels:
        support = sum(1 for o in outcomes if o.case.expected == lbl)
        prec_d = tp[lbl] + fp[lbl]
        rec_d = tp[lbl] + fn[lbl]
        print(
            f"  {lbl:12s}  P {_pct(tp[lbl], prec_d)}   R {_pct(tp[lbl], rec_d)}"
            f"   (support {support})"
        )
    print()
    print("list-my-wikis routing check (NOT admin, routable -> router list_wikis path):")
    list_cases = [o for o in outcomes if o.case.list_route]
    for o in list_cases:
        ok = o not in list_route_fail
        print(f"  [{'PASS' if ok else 'FAIL'}] {o.predicted!s:12s}  {o.case.text}")
    print()
    print(f"MISSES ({len(misses)}):")
    for o in misses:
        print(f"  exp={o.case.expected:12s} got={o.predicted!s:12s} :: {o.case.text}")
    if any(o.error for o in outcomes):
        print()
        print("ERRORS:")
        for o in outcomes:
            if o.error:
                print(f"  {o.error} :: {o.case.text}")
    print("=" * 72)
    return not list_route_fail


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt", default=str(_REPO_ROOT / "prompts" / "classifier.md"))
    ap.add_argument(
        "--corpus",
        default=str(_REPO_ROOT / "tests" / "fixtures" / "classifier_corpus.jsonl"),
    )
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=45.0)
    args = ap.parse_args()

    prompt_path = Path(args.prompt).resolve()
    corpus = load_corpus(Path(args.corpus).resolve())
    backend = ClaudeCliBackend(
        claude_config_dir=default_claude_config_dir(),
        timeout_s=args.timeout,
    )
    cache = PromptCache()
    sem = asyncio.Semaphore(args.concurrency)

    print(f"prompt : {prompt_path}  (semver {cache.get(prompt_path).semver})")
    print(f"corpus : {len(corpus)} cases")
    print(f"backend: {backend.name} / {backend.model}  timeout={args.timeout}s")
    print("running real Haiku classifications (paid, nondeterministic) ...\n")

    outcomes = await asyncio.gather(
        *(
            classify_one(c, backend=backend, prompt_path=prompt_path, cache=cache, sem=sem, idx=i)
            for i, c in enumerate(corpus)
        )
    )
    ok = report(list(outcomes))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
