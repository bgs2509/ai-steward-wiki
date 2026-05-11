"""Tier-2 MASK: email/phone/IBAN normalisation + deterministic HMAC hash."""

from __future__ import annotations

import re

import pytest

from ai_steward_wiki.ops.pii import PIIRedactor

SECRET = b"unit-test-salt"


@pytest.fixture
def r() -> PIIRedactor:
    return PIIRedactor(hash_secret=SECRET)


def _extract(marker_kind: str, text: str) -> str:
    m = re.search(rf"\[MASK:tier2:{marker_kind}:([0-9a-f]{{16}})\]", text)
    assert m, f"no {marker_kind} mask in {text!r}"
    return m.group(1)


def test_email_masked(r: PIIRedactor) -> None:
    out = r.redact("user@example.com")
    assert out.startswith("[MASK:tier2:email:")
    assert out.endswith("]")


def test_email_deterministic(r: PIIRedactor) -> None:
    a = _extract("email", r.redact("user@example.com"))
    b = _extract("email", r.redact("USER@example.com"))  # case normalised
    assert a == b


def test_email_distinct_for_different_inputs(r: PIIRedactor) -> None:
    a = _extract("email", r.redact("a@x.com"))
    b = _extract("email", r.redact("b@x.com"))
    assert a != b


def test_phone_e164_and_local_normalise_equal(r: PIIRedactor) -> None:
    h1 = _extract("phone", r.redact("call +7 916 123-45-67 now"))
    h2 = _extract("phone", r.redact("call 8 (916) 123-45-67 now"))
    assert h1 == h2


def test_iban_valid_mod97_masked(r: PIIRedactor) -> None:
    # Valid IBAN (Deutsche Bank example).
    out = r.redact("IBAN DE89370400440532013000 done")
    assert "[MASK:tier2:iban:" in out


def test_iban_invalid_not_masked(r: PIIRedactor) -> None:
    out = r.redact("XX99000000000000000000")
    assert "[MASK:tier2:iban:" not in out


def test_hash_changes_with_secret() -> None:
    a = PIIRedactor(hash_secret=b"salt-a")
    b = PIIRedactor(hash_secret=b"salt-b")
    ha = _extract("email", a.redact("u@x.com"))
    hb = _extract("email", b.redact("u@x.com"))
    assert ha != hb


def test_mask_disabled_keeps_text() -> None:
    r2 = PIIRedactor(hash_secret=SECRET, mask_enabled=False)
    assert r2.redact("u@x.com") == "u@x.com"
