# FILE: src/ai_steward_wiki/scheduler/queue_payloads.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: In-memory PriorityJobQueue message types (Pydantic v2 discriminated union).
#   SCOPE: CronUserQueueMsg (kind='cron_user'); CheckInQueueMsg (kind='check_in');
#          QueueMsg TypeAdapter (NFR-5). Discriminator left in place so future
#          kinds extend without widening callers.
#   DEPENDS: pydantic v2, datetime
#   LINKS: M-SCHEDULER-CONSUMER, M-SCHEDULER-CRON-USER, aisw-02v, aisw-xi8, D-011 §3
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CronUserQueueMsg - cron-user fire payload (job_id, owner_telegram_id, chat_id,
#                      command, correlation_id, scheduled_at_utc UTC-aware)
#   CheckInQueueMsg - check-in fire payload (job_id, owner_telegram_id, chat_id,
#                     question_topic, correlation_id, scheduled_at_utc UTC-aware)
#   QueueMsg - Annotated discriminated union (CronUserQueueMsg | CheckInQueueMsg)
#   parse_queue_msg - validate a dict into the union (TypeAdapter)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - aisw-xi8 (Phase-B, DEC-6/DEC-8): +CheckInQueueMsg —
#                additive QueueMsg union member (the discriminator was explicitly
#                reserved for future kinds, NFR-5). CronUserQueueMsg unchanged.
#   PREVIOUS:    v0.0.1 - aisw-02v: initial PriorityJobQueue payload union
# END_CHANGE_SUMMARY

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = ["CheckInQueueMsg", "CronUserQueueMsg", "QueueMsg", "parse_queue_msg"]


class _MsgBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CronUserQueueMsg(_MsgBase):
    """Cron-user fire payload pushed by M-SCHEDULER-CRON-USER → drained by M-SCHEDULER-CONSUMER."""

    kind: Literal["cron_user"] = "cron_user"
    job_id: int
    owner_telegram_id: int
    chat_id: int
    command: str
    correlation_id: str
    scheduled_at_utc: datetime  # UTC-aware (callers pass datetime.now(UTC))


class CheckInQueueMsg(_MsgBase):
    """Check-in fire payload pushed by M-SCHEDULER-CRON-USER -> drained by
    M-SCHEDULER-CONSUMER (aisw-xi8, DEC-6/DEC-8)."""

    kind: Literal["check_in"] = "check_in"
    job_id: int
    owner_telegram_id: int
    chat_id: int
    question_topic: str
    correlation_id: str
    scheduled_at_utc: datetime


QueueMsg = Annotated[CronUserQueueMsg | CheckInQueueMsg, Field(discriminator="kind")]
_adapter: TypeAdapter[QueueMsg] = TypeAdapter(QueueMsg)


def parse_queue_msg(value: dict[str, Any]) -> QueueMsg:
    return _adapter.validate_python(value)
