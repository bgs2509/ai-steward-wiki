"""redact() idempotency and composability."""

from __future__ import annotations

from ai_steward_wiki.ops.pii import PIIRedactor, redact


def test_redact_idempotent() -> None:
    r = PIIRedactor(hash_secret=b"k")
    s = "user@example.com card 4111 1111 1111 1111 token=secretXYZ12345"  # gitleaks:allow
    once = r.redact(s)
    twice = r.redact(once)
    assert once == twice


def test_module_level_redact_callable() -> None:
    out = redact("u@x.com", redactor=PIIRedactor(hash_secret=b"k"))
    assert out.startswith("[MASK:tier2:email:")


def test_redact_handles_clean_string() -> None:
    r = PIIRedactor(hash_secret=b"k")
    assert r.redact("привет, мир") == "привет, мир"
