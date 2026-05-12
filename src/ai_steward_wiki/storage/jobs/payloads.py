# FILE: src/ai_steward_wiki/storage/jobs/payloads.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: Pydantic v2 discriminated union for jobs.payload (D-002).
#   SCOPE: Closed list of job kinds known at MVP. New kinds added in subsequent chunks.
#   DEPENDS: pydantic v2
#   LINKS: M-STORAGE-JOBS, M-SCHEDULER, M-SCHEDULER-FIRING
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   WikiRunPayload - one-shot Stage-1a/1b run against a Domain-WIKI
#   DigestPayload - scheduled digest job (D-024)
#   CronUserPayload - user-defined cron (NL-scheduled)
#   PurgePayload - retention purge job (D-034 / §10.4)
#   ReminderPayload - one-shot reminder job: message + optional lead_time_min (aisw-kcz)
#   JobPayload - Annotated discriminated union over the five above
#   parse_job_payload - validate a dict into the union, returning the concrete model
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-kcz: add ReminderPayload (kind='reminder_job') to the union
#   PREVIOUS:    v0.0.2 - initial discriminated union for job payloads (D-002)
# END_CHANGE_SUMMARY

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = [
    "CronUserPayload",
    "DigestPayload",
    "JobPayload",
    "PurgePayload",
    "ReminderPayload",
    "WikiRunPayload",
    "parse_job_payload",
]


class _PayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WikiRunPayload(_PayloadBase):
    kind: Literal["wiki_run"] = "wiki_run"
    wiki_id: str
    prompt_text: str
    correlation_id: str


class DigestPayload(_PayloadBase):
    kind: Literal["digest"] = "digest"
    wiki_id: str
    window_hours: int = Field(ge=1, le=24 * 7)


class CronUserPayload(_PayloadBase):
    kind: Literal["cron_user"] = "cron_user"
    wiki_id: str
    cron_expr: str
    user_text: str


class PurgePayload(_PayloadBase):
    kind: Literal["purge"] = "purge"
    target: str  # e.g. "audit.chat_log", "sessions.pending_users"
    older_than_hours: int = Field(ge=1)


class ReminderPayload(_PayloadBase):
    kind: Literal["reminder_job"] = "reminder_job"
    message: str
    lead_time_min: int = Field(default=0, ge=0)


JobPayload = Annotated[
    WikiRunPayload | DigestPayload | CronUserPayload | PurgePayload | ReminderPayload,
    Field(discriminator="kind"),
]

_adapter: TypeAdapter[JobPayload] = TypeAdapter(JobPayload)


def parse_job_payload(value: dict[str, Any]) -> JobPayload:
    return _adapter.validate_python(value)
