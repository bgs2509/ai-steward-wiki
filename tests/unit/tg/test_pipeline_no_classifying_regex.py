"""FR-2 regression guard: the classifying Python forks deleted in Phase C.1
(_RECURRING_KEYWORDS punt, _DIGEST_DISABLE_RE/_DIGEST_RESCHEDULE_RE/
_detect_digest_action) must never be reintroduced — Haiku is the SOLE
classifier (ADR-035)."""

from __future__ import annotations

from pathlib import Path

_PIPELINE_SRC = (
    Path(__file__).resolve().parents[3] / "src" / "ai_steward_wiki" / "tg" / "pipeline.py"
)

_FORBIDDEN_SYMBOLS = (
    "_RECURRING_KEYWORDS",
    "_DIGEST_DISABLE_RE",
    "_DIGEST_RESCHEDULE_RE",
    "_detect_digest_action",
    "_dispatch_digest_disable",
    "_dispatch_digest_reschedule",
    "REMINDER_CONFIDENCE_THRESHOLD",  # renamed to CLASSIFIER_CONFIDENCE_THRESHOLD
)


def test_classifying_regex_forks_absent_from_pipeline_source() -> None:
    source = _PIPELINE_SRC.read_text(encoding="utf-8")
    found = [sym for sym in _FORBIDDEN_SYMBOLS if sym in source]
    assert not found, f"deleted classifying-fork symbols reappeared: {found}"


def test_hhmm_validators_survive_fr3() -> None:
    """FR-3: the two parameter-validator helpers must NOT be deleted alongside
    their classifying siblings."""
    source = _PIPELINE_SRC.read_text(encoding="utf-8")
    for kept in ("_extract_hhmm", "_extract_lead_minutes", "CLASSIFIER_CONFIDENCE_THRESHOLD"):
        assert kept in source, f"FR-3-protected symbol missing: {kept}"
