from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_steward_wiki.classifier import (
    ClassifierError,
    ClassifierResult,
    ClassifierSchemaError,
    ClassifierTimeoutError,
    Intent,
    TimeParseResult,
)


def _ok_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "intent": "reminder",
        "confidence": 0.92,
        "distilled_payload": {"when": "tomorrow 9am"},
        "backend": "fake",
        "model": "fake-haiku",
        "prompt_semver": "1.0.0",
        "prompt_sha256": "a" * 64,
        "latency_ms": 42,
    }
    base.update(overrides)
    return base


def test_intent_enum_closed() -> None:
    assert {i.value for i in Intent} == {
        "reminder",
        "wiki_ingest",
        "wiki_query",
        "wiki_lint",
        "digest",
        "web_task",
        "admin",
        "unknown",
    }


def test_classifier_result_happy() -> None:
    r = ClassifierResult.model_validate(_ok_payload())
    assert r.intent is Intent.REMINDER
    assert r.confidence == 0.92


def test_classifier_result_rejects_extra() -> None:
    with pytest.raises(ValidationError):
        ClassifierResult.model_validate(_ok_payload(unknown_field=1))


def test_classifier_result_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        ClassifierResult.model_validate(_ok_payload(confidence=1.5))
    with pytest.raises(ValidationError):
        ClassifierResult.model_validate(_ok_payload(confidence=-0.1))


def test_classifier_result_frozen() -> None:
    r = ClassifierResult.model_validate(_ok_payload())
    with pytest.raises(ValidationError):
        r.confidence = 0.1  # type: ignore[misc]


def test_time_parse_result_escalate() -> None:
    r = TimeParseResult(
        when_utc=None,
        source="escalate",
        escalate=True,
        raw="когда-нибудь",
        user_tz="Europe/Moscow",
    )
    assert r.escalate is True
    assert r.when_utc is None


def test_error_hierarchy() -> None:
    assert issubclass(ClassifierTimeoutError, ClassifierError)
    assert issubclass(ClassifierSchemaError, ClassifierError)
