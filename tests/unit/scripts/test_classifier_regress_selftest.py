"""Selftest for scripts/classifier_regress.py's pure scoring logic (aisw-xi8, DEC-13).

No network, no real CLI — exercises score_verdict/render_report against a small
FIXTURE corpus to verify the gate arithmetic (accept-list honouring, per-cluster
breakdown, exit-code decision) independent of live Haiku output.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from classifier_regress import CorpusCase, render_report, score_verdict, write_stamp


def _case(id_: int, expected: dict, accept: tuple = ()) -> CorpusCase:
    return CorpusCase(id=id_, text=f"case {id_}", expected=expected, accept=accept)


def test_score_verdict_exact_match() -> None:
    case = _case(1, {"intent": "wiki", "action": "ingest"})
    v = score_verdict(case, intent="wiki", action="ingest", kind=None, distilled_payload={})
    assert v.intent_ok is True
    assert v.full_ok is True


def test_score_verdict_intent_mismatch() -> None:
    case = _case(2, {"intent": "wiki", "action": "ingest"})
    v = score_verdict(case, intent="job", action="create", kind="once", distilled_payload={})
    assert v.intent_ok is False
    assert v.full_ok is False


def test_score_verdict_intent_ok_action_mismatch() -> None:
    case = _case(3, {"intent": "wiki", "action": "catalog"})
    v = score_verdict(case, intent="wiki", action=None, kind=None, distilled_payload={})
    assert v.intent_ok is True
    assert v.full_ok is False


def test_score_verdict_honours_accept_list() -> None:
    case = _case(
        4, {"intent": "wiki", "action": "catalog"}, accept=({"intent": "wiki", "action": None},)
    )
    v = score_verdict(case, intent="wiki", action=None, kind=None, distilled_payload={})
    assert v.intent_ok is True
    assert v.full_ok is True  # accepted via the accept-list entry


def test_write_stamp_records_prompt_sha256(tmp_path: Path) -> None:
    prompt_path = tmp_path / "classifier.md"
    prompt_path.write_text("prompt v1", encoding="utf-8")
    stamp_path = tmp_path / ".classifier_regress.stamp"

    write_stamp(prompt_path, stamp_path)

    expected = hashlib.sha256(b"prompt v1").hexdigest()
    assert stamp_path.read_text(encoding="utf-8").strip() == expected


def test_write_stamp_overwrites_on_prompt_change(tmp_path: Path) -> None:
    prompt_path = tmp_path / "classifier.md"
    stamp_path = tmp_path / ".classifier_regress.stamp"
    prompt_path.write_text("prompt v1", encoding="utf-8")
    write_stamp(prompt_path, stamp_path)

    prompt_path.write_text("prompt v2", encoding="utf-8")
    write_stamp(prompt_path, stamp_path)

    expected = hashlib.sha256(b"prompt v2").hexdigest()
    assert stamp_path.read_text(encoding="utf-8").strip() == expected


def test_score_verdict_error_is_a_full_miss() -> None:
    case = _case(5, {"intent": "web"})
    v = score_verdict(case, intent=None, action=None, kind=None, distilled_payload={}, error="boom")
    assert v.intent_ok is False
    assert v.full_ok is False
    assert v.error == "boom"


def test_score_verdict_action_not_required_when_expected_omits_it() -> None:
    """A management action (list/cancel/reschedule) expected dict has no 'kind' key
    — kind must not be checked when the expected candidate omits it."""
    case = _case(6, {"intent": "job", "action": "list"})
    v = score_verdict(case, intent="job", action="list", kind="once", distilled_payload={})
    assert v.full_ok is True


def test_verbatim_violation_detected() -> None:
    case = _case(7, {"intent": "job", "action": "create", "kind": "once"})
    v = score_verdict(
        case,
        intent="job",
        action="create",
        kind="once",
        distilled_payload={"time_expr": "in 5 minutes"},
    )
    assert v.verbatim_ok is False


def test_verbatim_ok_when_substring_present() -> None:
    case = CorpusCase(
        id=8,
        text="напомни через 5 минут",
        expected={"intent": "job", "action": "create", "kind": "once"},
    )
    v = score_verdict(
        case,
        intent="job",
        action="create",
        kind="once",
        distilled_payload={"time_expr": "через 5 минут"},
    )
    assert v.verbatim_ok is True


def test_render_report_gate_passes_at_100_intent_99_full() -> None:
    verdicts = []
    for i in range(1, 101):
        case = _case(i, {"intent": "wiki", "action": "ingest"})
        full_ok = i != 1  # exactly 1 action miss out of 100 -> 99% full accuracy
        verdicts.append(
            score_verdict(
                case,
                intent="wiki",
                action="ingest" if full_ok else "query",
                kind=None,
                distilled_payload={},
            )
        )
    report, passed = render_report(verdicts)
    assert passed is True
    assert "GATE: PASS" in report


def test_render_report_gate_fails_below_intent_threshold() -> None:
    verdicts = [
        score_verdict(
            _case(1, {"intent": "wiki"}), intent="job", action=None, kind=None, distilled_payload={}
        )
    ]
    report, passed = render_report(verdicts)
    assert passed is False
    assert "GATE: FAIL" in report


def test_render_report_lists_misses_and_per_cluster() -> None:
    verdicts = [
        score_verdict(
            _case(1, {"intent": "web"}), intent="chat", action=None, kind=None, distilled_payload={}
        )
    ]
    report, _ = render_report(verdicts)
    assert "per-cluster" in report
    assert "misses" in report
