# FILE: src/ai_steward_wiki/llm/failover.py
# VERSION: 0.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Select Claude, Codex, or one Claude probe and permit fallback only for typed limits and proven-safe replay.
#   SCOPE: ProviderState; ProviderLimitError; AttemptEvidence; process-local circuit;
#          single-flight probe; generic typed execution policy; structured transition logs.
#   DEPENDS: asyncio, dataclasses, datetime, enum, typing
#   LINKS: M-LLM-FAILOVER, M-LLM-CODEX, aisw-8gw, FR-1, FR-3, FR-7, FR-8, FR-9, FR-10
#   ROLE: RUNTIME
#   MAP_MODE: NONE
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.0 - aisw-8gw: contract-only planning stub.
# END_CHANGE_SUMMARY
