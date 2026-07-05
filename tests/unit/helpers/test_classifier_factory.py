"""RED-first coverage for the shared v2 ClassifierResult test factory (DEC-14)."""

from __future__ import annotations

from ai_steward_wiki.classifier.schema import Intent
from tests.helpers.classifier_factory import make_classifier_result


def test_factory_defaults() -> None:
    r = make_classifier_result(Intent.CHAT)
    assert r.intent is Intent.CHAT
    assert r.confidence == 0.95
    assert r.distilled_payload == {}
    assert r.backend == "fake"
    assert r.prompt_semver == "2.0.0"
    assert len(r.prompt_sha256) == 64


def test_factory_wiki_action_slot() -> None:
    r = make_classifier_result(Intent.WIKI, action="query")
    assert r.distilled_payload == {"action": "query"}


def test_factory_job_action_kind_and_extra_slots() -> None:
    r = make_classifier_result(Intent.JOB, action="create", kind="once", time_expr="через 5 минут")
    assert r.distilled_payload == {
        "action": "create",
        "kind": "once",
        "time_expr": "через 5 минут",
    }


def test_factory_confidence_override() -> None:
    r = make_classifier_result(Intent.JOB, action="cancel", confidence=0.5)
    assert r.confidence == 0.5


def test_factory_no_action_no_kind_omits_both_keys() -> None:
    r = make_classifier_result(Intent.UNKNOWN)
    assert "action" not in r.distilled_payload
    assert "kind" not in r.distilled_payload
