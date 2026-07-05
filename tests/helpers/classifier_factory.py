# FILE: tests/helpers/classifier_factory.py
"""Shared v2 ClassifierResult test factory (aisw-xi8, DEC-14).

Every downstream test across Phases B/C/C.4 constructs v2 ClassifierResults
through this one factory, so a future taxonomy change touches one place.
"""

from __future__ import annotations

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent


def make_classifier_result(
    intent: Intent,
    *,
    action: str | None = None,
    kind: str | None = None,
    confidence: float = 0.95,
    **extra_slots: object,
) -> ClassifierResult:
    """Build a v2 ClassifierResult with the given intent and distilled_payload slots.

    ``action``/``kind`` are omitted from ``distilled_payload`` entirely when None
    (not written as null) — matching how a real Stage-0 reply for an intent that
    doesn't use a given slot omits it. ``extra_slots`` covers time_expr/
    schedule_expr/text/needle and any future slot without widening this factory's
    signature.
    """
    payload: dict[str, object] = {}
    if action is not None:
        payload["action"] = action
    if kind is not None:
        payload["kind"] = kind
    payload.update(extra_slots)
    return ClassifierResult(
        intent=intent,
        confidence=confidence,
        distilled_payload=payload,
        backend="fake",
        model="fake-haiku",
        prompt_semver="2.0.0",
        prompt_sha256="a" * 64,
        latency_ms=1,
    )
