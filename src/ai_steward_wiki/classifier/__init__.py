# FILE: src/ai_steward_wiki/classifier/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Public surface of the Stage-0 classifier module (M-CLASSIFIER-STAGE0, chunk 5).
#   SCOPE: Re-export schema, backends, classify, parse_time, errors.
#   DEPENDS: ai_steward_wiki.classifier.{schema,backend,stage0,time_parse}
#   LINKS: M-CLASSIFIER-STAGE0
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Intent - closed enum of Stage-0 intents
#   ClassifierResult - frozen result schema with audit fields
#   TimeParseResult - frozen NL-time result schema with escalate flag
#   ClassifierBackend - Protocol for backends
#   Spawner - Protocol for subprocess spawn primitive (chunk 16 systemd-run seam)
#   AsyncioSpawner - default Spawner using asyncio.create_subprocess_exec
#   ClaudeCliBackend - default subprocess backend (Haiku CLI)
#   FailoverClassifierBackend - Claude-first structured Codex fallback backend
#   AnthropicApiBackend - optional API backend (chunk 16 wires call())
#   FakeClaudeRunner - deterministic test double
#   classify - Stage-0 orchestrator
#   parse_time - NL time parser (dateparser → Haiku-fallback → escalate)
#   PromptCache - per-process prompt cache (semver+sha256)
#   record_prompt_version - idempotent upsert into audit.prompt_versions
#   ClassifierError - base error for module
#   ClassifierTimeoutError - transient subclass (chunk 4 taxonomy)
#   ClassifierSchemaError - permanent subclass for schema violations
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-8gw: export FailoverClassifierBackend.
#   PREVIOUS:    v0.0.1 - initial barrel for chunk 5
# END_CHANGE_SUMMARY

from ai_steward_wiki.classifier.backend import (
    AnthropicApiBackend,
    AsyncioSpawner,
    ClassifierBackend,
    ClaudeCliBackend,
    FailoverClassifierBackend,
    FakeClaudeRunner,
    Spawner,
)
from ai_steward_wiki.classifier.schema import (
    ClassifierError,
    ClassifierResult,
    ClassifierSchemaError,
    ClassifierTimeoutError,
    Intent,
    TimeParseResult,
)
from ai_steward_wiki.classifier.stage0 import PromptCache, classify, record_prompt_version
from ai_steward_wiki.classifier.time_parse import parse_time

__all__ = [
    "AnthropicApiBackend",
    "AsyncioSpawner",
    "ClassifierBackend",
    "ClassifierError",
    "ClassifierResult",
    "ClassifierSchemaError",
    "ClassifierTimeoutError",
    "ClaudeCliBackend",
    "FailoverClassifierBackend",
    "FakeClaudeRunner",
    "Intent",
    "PromptCache",
    "Spawner",
    "TimeParseResult",
    "classify",
    "parse_time",
    "record_prompt_version",
]
