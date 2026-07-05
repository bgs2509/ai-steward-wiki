"""RED-first slot-parsing coverage for WikiSlots/JobSlots/parse_slots (aisw-xi8, DEC-4)."""

from __future__ import annotations

from ai_steward_wiki.classifier.schema import JobSlots, WikiSlots, parse_slots


def test_wiki_slots_action_none_is_default_and_valid() -> None:
    """Covers the measured 99/100 miss: "Покажи мои вики" -> action=None still
    routes correctly downstream (DEC-3's routable predicate treats a missing
    action as routable, same as action="catalog")."""
    s = WikiSlots()
    assert s.action is None


def test_wiki_slots_all_actions_valid() -> None:
    for action in ("ingest", "query", "lint", "catalog"):
        assert WikiSlots(action=action).action == action


def test_job_slots_defaults_create_once() -> None:
    s = JobSlots()
    assert s.action == "create"
    assert s.kind == "once"
    assert s.time_expr == ""
    assert s.schedule_expr == ""
    assert s.text == ""
    assert s.needle == ""


def test_job_slots_verbatim_roundtrip_no_normalisation() -> None:
    """FR-12: free-text slots never get whitespace/case-normalised or translated."""
    s = JobSlots(
        time_expr="  через 5 Минут  ",
        schedule_expr="каждый день в 9",
        text="ПОЙТИ гулять",
        needle="про Таблетки",
    )
    assert s.time_expr == "  через 5 Минут  "
    assert s.schedule_expr == "каждый день в 9"
    assert s.text == "ПОЙТИ гулять"
    assert s.needle == "про Таблетки"


def test_job_slots_all_kinds_and_actions_valid() -> None:
    for kind in ("once", "recurring", "check_in", "digest"):
        assert JobSlots(kind=kind).kind == kind
    for action in ("create", "cancel", "list", "reschedule"):
        assert JobSlots(action=action).action == action


def test_parse_slots_well_formed_dict_validates() -> None:
    result = parse_slots(JobSlots, {"action": "cancel", "needle": "про таблетки"})
    assert result == JobSlots(action="cancel", needle="про таблетки")


def test_parse_slots_unknown_keys_ignored() -> None:
    """A JobSlots-only payload carrying a WikiSlots-shaped key must not raise —
    it is silently dropped before validation."""
    result = parse_slots(WikiSlots, {"action": "ingest", "needle": "unrelated"})
    assert result == WikiSlots(action="ingest")


def test_parse_slots_malformed_value_returns_default_never_raises() -> None:
    """A well-typed-but-invalid Literal value (action=123, not a string) must
    degrade to the default instance, never raise ValidationError to the caller."""
    result = parse_slots(WikiSlots, {"action": 123})
    assert result == WikiSlots()


def test_parse_slots_logs_invalid_anchor(capsys: object) -> None:
    parse_slots(JobSlots, {"kind": "not-a-real-kind"})
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "classifier.slots.invalid" in out
    assert "JobSlots" in out


def test_parse_slots_empty_dict_returns_default() -> None:
    assert parse_slots(JobSlots, {}) == JobSlots()
    assert parse_slots(WikiSlots, {}) == WikiSlots()
