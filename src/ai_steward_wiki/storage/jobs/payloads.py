# FILE: src/ai_steward_wiki/storage/jobs/payloads.py
# VERSION: 0.0.8
# START_MODULE_CONTRACT
#   PURPOSE: Pydantic v2 discriminated union for jobs.payload (D-002).
#   SCOPE: Closed list of job kinds known at MVP. New kinds added in subsequent chunks.
#   DEPENDS: pydantic v2, ai_steward_wiki.classifier.recurrence (Recurrence)
#   LINKS: M-STORAGE-JOBS, M-SCHEDULER, M-SCHEDULER-FIRING, M-CLASSIFIER-RECURRENCE
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   WikiRunPayload - one-shot Stage-1a/1b run against a Domain-WIKI
#   DigestPayload - recurring digest job: wiki_scope ('all'|list[str]), recurrence, window_hours, prompt_hint (aisw-oqq; list shape aisw-269)
#   CronUserPayload - user-defined NL-scheduled cron (recurrence:Recurrence, command, wiki_id?) — aisw-02v
#   PurgePayload - retention purge job (D-034 / §10.4)
#   ReminderPayload - one-shot reminder job: message + optional lead_time_min + category (aisw-kcz; category aisw-163)
#   RecurringReminderPayload - fixed-text cron reminder: message + recurrence + category (aisw-xi8, DEC-6)
#   CheckInPayload - bot-generated recurring question: question_topic + recurrence + wiki_id? (aisw-xi8, DEC-6)
#   JobPayload - Annotated discriminated union over the seven above
#   parse_job_payload - validate a dict into the union, returning the concrete model
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.8 - aisw-xi8 (Phase-B, DEC-6): two ADDITIVE JobPayload union
#                members — RecurringReminderPayload(kind='recurring_reminder') and
#                CheckInPayload(kind='check_in'). No Alembic migration (JSON column,
#                additive discriminator tag). Existing 5 kinds unchanged (FR-15).
#   PREVIOUS:    v0.0.7 - aisw-02v: widen CronUserPayload — typed Recurrence (was cron_expr:str) +
#                free-form command (was user_text) + optional wiki_id (was required str); AD-05 no
#                Alembic migration (JSON column, zero existing rows with kind='cron_user').
#   PREVIOUS:    v0.0.6 - aisw-163: add ReminderPayload.category (medication|event|generic; default 'generic'; legacy rows keep validating)
#   PREVIOUS:    v0.0.5 - aisw-269: widen DigestPayload.wiki_scope to 'all'|list[str] (named-subset digest; no jobs.db migration)
#   PREVIOUS:    v0.0.4 - aisw-oqq: widen DigestPayload (wiki_scope/recurrence/window_hours/prompt_hint)
#   PREVIOUS:    v0.0.3 - aisw-kcz: add ReminderPayload (kind='reminder_job') to the union
#   PREVIOUS:    v0.0.2 - initial discriminated union for job payloads (D-002)
# END_CHANGE_SUMMARY

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ai_steward_wiki.classifier.recurrence import Recurrence

__all__ = [
    "CheckInPayload",
    "CronUserPayload",
    "DigestPayload",
    "JobPayload",
    "PurgePayload",
    "RecurringReminderPayload",
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
    # 'all' (every owner WIKI minus Inbox) or an explicit non-empty list of
    # WIKI dir-stems (aisw-269; no jobs.db migration — 'all' stays valid).
    wiki_scope: Literal["all"] | Annotated[list[str], Field(min_length=1)] = "all"
    recurrence: Recurrence
    window_hours: int = Field(default=24, ge=1, le=24 * 7)
    prompt_hint: str | None = None


class CronUserPayload(_PayloadBase):
    """User-defined NL-scheduled cron job payload (aisw-02v).

    Widened from the pre-aisw-02v shape (wiki_id:str + cron_expr:str + user_text:str)
    to mirror DigestPayload's typed-Recurrence convention. JSON column, no Alembic
    migration needed (zero rows with kind='cron_user' existed prior to widening).
    """

    kind: Literal["cron_user"] = "cron_user"
    recurrence: Recurrence
    command: str
    wiki_id: str | None = None


class PurgePayload(_PayloadBase):
    kind: Literal["purge"] = "purge"
    target: str  # e.g. "audit.chat_log", "sessions.pending_users"
    older_than_hours: int = Field(ge=1)


class ReminderPayload(_PayloadBase):
    kind: Literal["reminder_job"] = "reminder_job"
    message: str
    lead_time_min: int = Field(default=0, ge=0)
    # aisw-163: lets the digest cards module render category-specific button
    # sets. Legacy rows (persisted before this field existed) default to
    # 'generic' on parse — no jobs.db migration needed for the payload itself.
    category: Literal["medication", "event", "generic"] = "generic"


class RecurringReminderPayload(_PayloadBase):
    """Fixed-text cron reminder (aisw-xi8, DEC-6/DEC-7). Delivered VERBATIM on
    every fire — plain TG send, no Claude CLI call (NFR-2)."""

    kind: Literal["recurring_reminder"] = "recurring_reminder"
    message: str
    recurrence: Recurrence
    category: Literal["medication", "event", "generic"] = "generic"


class CheckInPayload(_PayloadBase):
    """Bot-generated recurring question (aisw-xi8, DEC-6/DEC-8). The CLI at fire
    time generates ONE ru question about question_topic; on failure a
    deterministic ru fallback is sent verbatim (FR-6)."""

    kind: Literal["check_in"] = "check_in"
    question_topic: str
    recurrence: Recurrence
    wiki_id: str | None = None


JobPayload = Annotated[
    WikiRunPayload
    | DigestPayload
    | CronUserPayload
    | PurgePayload
    | ReminderPayload
    | RecurringReminderPayload
    | CheckInPayload,
    Field(discriminator="kind"),
]

_adapter: TypeAdapter[JobPayload] = TypeAdapter(JobPayload)


def parse_job_payload(value: dict[str, Any]) -> JobPayload:
    return _adapter.validate_python(value)
