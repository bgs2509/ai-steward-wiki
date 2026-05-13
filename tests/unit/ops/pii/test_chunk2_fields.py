"""PII redactor leaves chunk-2 metadata fields alone, still redacts free-text.

Asserts that PIIRedactor.redact_event passes through bytes-counts, sha8 hex,
ids and labels unchanged, AND still drops a bearer token / masks an email
that happens to land in a free-text field of the same record.
"""

from __future__ import annotations

from ai_steward_wiki.ops.pii import PIIRedactor


def _representative_record() -> dict[str, object]:
    return {
        # Scheduler chunk-2 fields
        "event": "scheduler.job.executed",
        "job_id": "job-42",
        "jobstore": "default",
        "scheduled_run_time": "2026-05-13T12:00:00+00:00",
        "duration_ms": 123,
        # Storage chunk-2 fields
        "db_name": "jobs",
        "statement_sha8": "a1b2c3d4",
        # Claude CLI chunk-2 fields
        "argv_length": 12,
        "env_keys_count": 4,
        "cwd": "/var/lib/ai-steward-wiki/claude-code",
        "exit_code": 0,
        "stdout_bytes": 1024,
        "stderr_bytes": 0,
        "reason": "nonzero_exit",
        # Existing pipeline fields
        "correlation_id": "8c9d0e1f-2a3b-4c5d-6e7f-8091a2b3c4d5",
    }


def test_chunk2_metadata_fields_pass_through_unchanged() -> None:
    rec = _representative_record()
    expected = dict(rec)
    out = PIIRedactor().redact_event(dict(rec))
    for key in (
        "event",
        "job_id",
        "jobstore",
        "scheduled_run_time",
        "duration_ms",
        "db_name",
        "statement_sha8",
        "argv_length",
        "env_keys_count",
        "exit_code",
        "stdout_bytes",
        "stderr_bytes",
        "reason",
        "correlation_id",
    ):
        assert out[key] == expected[key], f"field {key!r} should be untouched"


def test_free_text_field_in_chunk2_record_still_redacts() -> None:
    rec = _representative_record()
    # Plant PII into a free-text field that might accidentally appear (e.g. an
    # operator-defined cwd containing an email, or a future "stderr_excerpt").
    rec["cwd"] = "/home/alice@example.com/wiki"
    rec["traceback"] = "Token Bearer abcdef1234567890ABCDEF leaked in stack frame"

    out = PIIRedactor().redact_event(rec)

    # Tier-2 MASK on the email shape inside cwd.
    assert "alice@example.com" not in str(out["cwd"])
    assert "[MASK:tier2:email:" in str(out["cwd"])
    # Tier-1 DROP on the bearer token inside traceback text.
    assert "abcdef1234567890" not in str(out["traceback"])
    assert "[REDACTED:tier1:bearer]" in str(out["traceback"])


def test_idempotent_on_chunk2_record() -> None:
    rec = _representative_record()
    once = PIIRedactor().redact_event(dict(rec))
    twice = PIIRedactor().redact_event(dict(once))
    assert once == twice
