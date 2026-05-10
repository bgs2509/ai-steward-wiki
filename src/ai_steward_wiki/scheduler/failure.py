# FILE: src/ai_steward_wiki/scheduler/failure.py
# VERSION: 0.0.4
# START_MODULE_CONTRACT
#   PURPOSE: Failure taxonomy + 3-strikes auto-disable counter (D-019, INV-12).
#   SCOPE: FailureClass enum, classify_exception heuristic, FailureCounter.
#   DEPENDS: stdlib only
#   LINKS: M-SCHEDULER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   FailureClass - Transient | Permanent | Unknown
#   classify_exception - exception → FailureClass heuristic
#   FailureCounter - per-job consecutive-failure tracker; reset only on success
# END_MODULE_MAP

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

AUTO_DISABLE_STRIKES = 3


class FailureClass(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


_PERMANENT_TYPES: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    LookupError,
)
_TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    TimeoutError,
    ConnectionError,
    OSError,
)


def classify_exception(exc: BaseException) -> FailureClass:
    if isinstance(exc, _TRANSIENT_TYPES):
        return FailureClass.TRANSIENT
    if isinstance(exc, _PERMANENT_TYPES):
        return FailureClass.PERMANENT
    return FailureClass.UNKNOWN


@dataclass
class FailureCounter:
    """Per-job consecutive-failure tracker.

    Strikes accumulate across all FailureClass values (D-019). Timeout (Transient)
    counts the same as Permanent (INV-12). Reset only on success.
    """

    threshold: int = AUTO_DISABLE_STRIKES
    strikes: int = 0
    history: list[FailureClass] = field(default_factory=list)

    def record_failure(self, cls: FailureClass) -> None:
        self.strikes += 1
        self.history.append(cls)

    def record_success(self) -> None:
        self.strikes = 0
        self.history.clear()

    @property
    def should_disable(self) -> bool:
        return self.strikes >= self.threshold
