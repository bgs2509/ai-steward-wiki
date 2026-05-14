# FILE: src/ai_steward_wiki/scheduler/queue_payloads.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: In-memory PriorityJobQueue message types (Pydantic v2 discriminated union).
#   SCOPE: CronUserQueueMsg (kind='cron_user'); QueueMsg TypeAdapter (NFR-5).
#          Discriminator left in place so future kinds extend without widening callers.
#   DEPENDS: pydantic v2, datetime
#   LINKS: M-SCHEDULER-CONSUMER, M-SCHEDULER-CRON-USER, aisw-02v, D-011 §3
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CronUserQueueMsg - cron-user fire payload (job_id, owner_telegram_id, chat_id,
#                      command, correlation_id, scheduled_at_utc UTC-aware)
#   QueueMsg - Annotated discriminated union (currently single member)
#   parse_queue_msg - validate a dict into the union (TypeAdapter)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-02v: initial PriorityJobQueue payload union
# END_CHANGE_SUMMARY

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = ["CronUserQueueMsg", "QueueMsg", "parse_queue_msg"]


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


QueueMsg = Annotated[CronUserQueueMsg, Field(discriminator="kind")]
_adapter: TypeAdapter[QueueMsg] = TypeAdapter(QueueMsg)


def parse_queue_msg(value: dict[str, Any]) -> QueueMsg:
    return _adapter.validate_python(value)
