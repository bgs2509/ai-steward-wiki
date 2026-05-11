"""Tier-1 DROP regex coverage."""

from __future__ import annotations

import pytest

from ai_steward_wiki.ops.pii import PIIRedactor


@pytest.fixture
def r() -> PIIRedactor:
    return PIIRedactor(hash_secret=b"test-secret")


def test_credit_card_with_spaces_redacted(r: PIIRedactor) -> None:
    # Valid Luhn: 4111 1111 1111 1111
    assert "[REDACTED:tier1:card]" in r.redact("PAN 4111 1111 1111 1111")


def test_credit_card_with_dashes_redacted(r: PIIRedactor) -> None:
    assert "[REDACTED:tier1:card]" in r.redact("card 4111-1111-1111-1111")


def test_invalid_luhn_not_redacted(r: PIIRedactor) -> None:
    out = r.redact("digits 1234567890123456")
    assert "[REDACTED:tier1:card]" not in out


def test_jwt_redacted(r: PIIRedactor) -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456"
    # JWT regex wins before the secret-kv regex; both are tier-1 outcomes.
    assert r.redact(f"raw {jwt} end") == "raw [REDACTED:tier1:jwt] end"


def test_pem_block_multiline_redacted(r: PIIRedactor) -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"  # gitleaks:allow
    assert r.redact(f"key={pem}\ntrailer") == "key=[REDACTED:tier1:pem]\ntrailer"


def test_bearer_header_redacted(r: PIIRedactor) -> None:
    out = r.redact("Authorization: Bearer abc123def456xyz")
    assert "[REDACTED:tier1:bearer]" in out


def test_kv_secrets_redacted(r: PIIRedactor) -> None:
    for kv in ["password=hunter2xyz", "api_key=ABCDEFG1234", "secret: topsecret"]:
        assert "[REDACTED:tier1:secret]" in r.redact(kv)


def test_ssn_redacted(r: PIIRedactor) -> None:
    assert r.redact("SSN 123-45-6789") == "SSN [REDACTED:tier1:ssn]"


def test_clean_text_untouched(r: PIIRedactor) -> None:
    assert r.redact("Это обычное предложение, 2026 год.") == "Это обычное предложение, 2026 год."


def test_uuid_not_treated_as_secret(r: PIIRedactor) -> None:
    cid = "550e8400-e29b-41d4-a716-446655440000"
    assert r.redact(cid) == cid


def test_drop_disabled_keeps_text(r: PIIRedactor) -> None:
    r2 = PIIRedactor(hash_secret=b"test-secret", drop_enabled=False, mask_enabled=False)
    raw = "Bearer abc123def456xyz"
    assert r2.redact(raw) == raw
