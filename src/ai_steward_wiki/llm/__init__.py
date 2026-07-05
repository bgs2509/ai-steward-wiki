# FILE: src/ai_steward_wiki/llm/__init__.py
# VERSION: 0.2.0
# START_MODULE_CONTRACT
#   PURPOSE: Public package boundary for subscription-backed LLM provider failover.
#   SCOPE: Re-export provider policy types and, after adapter implementation, Codex interfaces.
#   DEPENDS: ai_steward_wiki.llm.failover, ai_steward_wiki.llm.codex
#   LINKS: M-LLM-FAILOVER, M-LLM-CODEX, aisw-8gw
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   AttemptEvidence - fail-closed evidence for provider replay decisions
#   EvidenceKind - fixed side-effect evidence categories
#   FailoverEvent - sanitized provider transition event
#   FailoverMetrics - process-local provider outcome counters
#   FailoverPolicy - shared atomic Claude-to-Codex circuit
#   ProviderLimitError - typed provider subscription limit
#   ProviderState - approved claude, codex, and probe states
#   ProvidersUnavailableError - typed dual-provider failure
#   ReplayBlockedError - unsafe replay rejection
#   CodexCliAdapter - restricted subscription-backed Codex CLI adapter
#   CodexReadiness - non-model Codex readiness result
#   CodexRequest - validated provider invocation request
#   CodexRunKind - fixed Codex capability profiles
#   CodexUnavailableError - unavailable binary, subscription, or process error
#   CodexOutputError - malformed structured or JSONL output error
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 - aisw-8gw: export Codex adapter request, readiness,
#                capability, and error types.
#   PREVIOUS:    v0.1.0 - aisw-8gw: export implemented failover policy types.
#   PREVIOUS:    v0.0.0 - aisw-8gw: contract-only planning stub.
# END_CHANGE_SUMMARY

from ai_steward_wiki.llm.codex import (
    CodexCliAdapter,
    CodexOutputError,
    CodexReadiness,
    CodexRequest,
    CodexRunKind,
    CodexUnavailableError,
)
from ai_steward_wiki.llm.failover import (
    AttemptEvidence,
    EvidenceKind,
    FailoverEvent,
    FailoverMetrics,
    FailoverPolicy,
    ProviderLimitError,
    ProviderState,
    ProvidersUnavailableError,
    ReplayBlockedError,
)

__all__ = [
    "AttemptEvidence",
    "CodexCliAdapter",
    "CodexOutputError",
    "CodexReadiness",
    "CodexRequest",
    "CodexRunKind",
    "CodexUnavailableError",
    "EvidenceKind",
    "FailoverEvent",
    "FailoverMetrics",
    "FailoverPolicy",
    "ProviderLimitError",
    "ProviderState",
    "ProvidersUnavailableError",
    "ReplayBlockedError",
]
