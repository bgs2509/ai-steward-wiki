# FILE: src/ai_steward_wiki/scheduler/__init__.py
# VERSION: 0.0.4
# START_MODULE_CONTRACT
#   PURPOSE: Public surface of the scheduler core (M-SCHEDULER, chunk 4).
#   SCOPE: Re-export queue/lock/failure/dlq/core primitives.
#   DEPENDS: ai_steward_wiki.scheduler.{queue,locks,failure,dlq,core}
#   LINKS: M-SCHEDULER
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Lane - priority lane enum (0..4) per spec §3
#   QueueItem - tuple-orderable queue entry
#   PriorityJobQueue - asyncio.PriorityQueue with FIFO tiebreaker
#   WikiLockManager - 3-tier acquire (semaphore → memlock → flock)
#   FailureClass - Transient/Permanent/Unknown taxonomy (D-019)
#   FailureCounter - 3-strikes auto-disable (timeout counted)
#   classify_exception - heuristic exception → FailureClass
#   move_to_dlq - persist a failed job into jobs.db.jobs_dlq
#   kill_with_sequence - SIGTERM→grace→SIGKILL (D-021)
#   build_scheduler - APScheduler AsyncIOScheduler factory (D-003)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.4 - chunk 4: scheduler core barrel exports
# END_CHANGE_SUMMARY

from ai_steward_wiki.scheduler.core import build_scheduler, kill_with_sequence
from ai_steward_wiki.scheduler.dlq import move_to_dlq
from ai_steward_wiki.scheduler.failure import (
    FailureClass,
    FailureCounter,
    classify_exception,
)
from ai_steward_wiki.scheduler.locks import WikiLockManager
from ai_steward_wiki.scheduler.queue import Lane, PriorityJobQueue, QueueItem

__all__ = [
    "FailureClass",
    "FailureCounter",
    "Lane",
    "PriorityJobQueue",
    "QueueItem",
    "WikiLockManager",
    "build_scheduler",
    "classify_exception",
    "kill_with_sequence",
    "move_to_dlq",
]
