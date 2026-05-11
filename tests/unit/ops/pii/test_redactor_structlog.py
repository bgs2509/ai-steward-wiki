"""make_structlog_processor: integrated into chain, masks event-dict values."""

from __future__ import annotations

from ai_steward_wiki.ops.pii import PIIRedactor, make_structlog_processor


def test_processor_redacts_string_values() -> None:
    r = PIIRedactor(hash_secret=b"k")
    proc = make_structlog_processor(r)
    ed = {
        "event": "user signup",
        "email": "user@example.com",
        "token": "Bearer abc123def456xyz",
        "correlation_id": "cid-1",
    }
    out = proc(None, "info", ed)
    assert "user@example.com" not in str(out)
    assert "abc123def456xyz" not in str(out)
    assert "[MASK:tier2:email:" in out["email"]
    assert "[REDACTED:tier1:bearer]" in out["token"]
    assert out["correlation_id"] == "cid-1"


def test_processor_recurses_into_dicts_and_lists() -> None:
    r = PIIRedactor(hash_secret=b"k")
    proc = make_structlog_processor(r)
    ed = {"meta": {"emails": ["a@x.com", "b@x.com"]}}
    out = proc(None, "info", ed)
    assert "a@x.com" not in str(out)
    assert all("[MASK:tier2:email:" in e for e in out["meta"]["emails"])
