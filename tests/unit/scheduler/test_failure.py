from __future__ import annotations

import pytest

from ai_steward_wiki.scheduler.failure import (
    AUTO_DISABLE_STRIKES,
    FailureClass,
    FailureCounter,
    classify_exception,
)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError(), FailureClass.TRANSIENT),
        (ConnectionError("boom"), FailureClass.TRANSIENT),
        (OSError(2, "no entry"), FailureClass.TRANSIENT),
        (ValueError("bad"), FailureClass.PERMANENT),
        (TypeError("bad"), FailureClass.PERMANENT),
        (KeyError("k"), FailureClass.PERMANENT),
        (RuntimeError("?"), FailureClass.UNKNOWN),
    ],
)
def test_classify_exception(exc: BaseException, expected: FailureClass) -> None:
    assert classify_exception(exc) is expected


def test_counter_three_strikes_disables_regardless_of_class() -> None:
    counter = FailureCounter()
    counter.record_failure(FailureClass.TRANSIENT)
    counter.record_failure(FailureClass.PERMANENT)
    assert not counter.should_disable
    counter.record_failure(FailureClass.UNKNOWN)
    assert counter.should_disable
    assert counter.strikes == AUTO_DISABLE_STRIKES


def test_counter_timeout_counts_as_strike_inv12() -> None:
    counter = FailureCounter()
    for _ in range(AUTO_DISABLE_STRIKES):
        counter.record_failure(FailureClass.TRANSIENT)  # timeouts == Transient
    assert counter.should_disable


def test_counter_resets_only_on_success() -> None:
    counter = FailureCounter()
    counter.record_failure(FailureClass.TRANSIENT)
    counter.record_failure(FailureClass.TRANSIENT)
    counter.record_success()
    assert counter.strikes == 0
    assert not counter.should_disable
    counter.record_failure(FailureClass.PERMANENT)
    assert counter.strikes == 1
