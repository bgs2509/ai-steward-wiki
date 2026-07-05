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
from ai_steward_wiki.classifier.schema import unwrap_fenced_json


def _ok_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "intent": "job",
        "confidence": 0.92,
        "distilled_payload": {"action": "create", "kind": "once", "time_expr": "tomorrow 9am"},
        "backend": "fake",
        "model": "fake-haiku",
        "prompt_semver": "2.0.0",
        "prompt_sha256": "a" * 64,
        "latency_ms": 42,
    }
    base.update(overrides)
    return base


def test_intent_enum_closed() -> None:
    assert {i.value for i in Intent} == {
        "wiki",
        "job",
        "web",
        "chat",
        "admin",
        "unknown",
    }


def test_chat_intent_validates() -> None:
    """aisw-xi8: a chat-classified result is accepted by the schema (ex-SMALLTALK)."""
    r = ClassifierResult.model_validate(_ok_payload(intent="chat", confidence=0.9))
    assert r.intent is Intent.CHAT


def test_classifier_result_happy() -> None:
    r = ClassifierResult.model_validate(_ok_payload())
    assert r.intent is Intent.JOB
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


# --- unwrap_fenced_json (aisw-7j3) -----------------------------------------


def test_unwrap_plain_json_object() -> None:
    assert unwrap_fenced_json('{"when_iso": null, "ambiguous": true}') == {
        "when_iso": None,
        "ambiguous": True,
    }


def test_unwrap_strips_json_fence() -> None:
    fenced = '```json\n{"when_iso": "2026-05-11T09:00:00+03:00", "ambiguous": false}\n```'
    assert unwrap_fenced_json(fenced) == {
        "when_iso": "2026-05-11T09:00:00+03:00",
        "ambiguous": False,
    }


def test_unwrap_strips_bare_fence() -> None:
    assert unwrap_fenced_json('```\n{"ambiguous": true}\n```') == {"ambiguous": True}


def test_unwrap_tolerates_surrounding_prose() -> None:
    """A model that adds a sentence around the fence must still parse (real failure shape)."""
    reply = 'Вот результат:\n```json\n{"when_iso": null, "ambiguous": true}\n```\nГотово.'
    assert unwrap_fenced_json(reply) == {"when_iso": None, "ambiguous": True}


def test_unwrap_raises_on_non_json() -> None:
    with pytest.raises(ClassifierSchemaError):
        unwrap_fenced_json("мне нужно знать текущее время и часовой пояс")


def test_unwrap_raises_on_non_object() -> None:
    with pytest.raises(ClassifierSchemaError):
        unwrap_fenced_json("[1, 2, 3]")
