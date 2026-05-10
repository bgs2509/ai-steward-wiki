# FILE: src/ai_steward_wiki/wiki/__init__.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Stage-1a/1b Sonnet runner package — CLI subprocess + streaming +
#            strict acquire-order locks + atomic transcript persistence.
#   SCOPE: package marker; re-exports public API of submodules.
#   DEPENDS: ai_steward_wiki.scheduler (locks, kill-sequence)
#   LINKS: M-WIKI-RUNNER, D-007, D-011, D-012, D-021
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   StreamEvent - frozen Pydantic v2 model for one stream-json line
#   parse_stream_json - async iterator over StreamReader → StreamEvent
#   LockAcquirer - Protocol seam for tests
#   WikiLockAdapter - default LockAcquirer wrapping scheduler.locks.WikiLockManager
#   Spawner - Protocol seam for subprocess (test seam)
#   AsyncioSpawner - default Spawner using asyncio.create_subprocess_exec
#   run_wiki_session - main orchestrator (Stage-1a/1b)
#   WikiRunnerError - base exception
#   WikiRunnerTimeoutError - timeout failure (after kill-sequence)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 7: M-WIKI-RUNNER initial implementation
# END_CHANGE_SUMMARY

from ai_steward_wiki.wiki.acquire import LockAcquirer, WikiLockAdapter
from ai_steward_wiki.wiki.runner import (
    AsyncioSpawner,
    Spawner,
    WikiRunnerError,
    WikiRunnerTimeoutError,
    run_wiki_session,
)
from ai_steward_wiki.wiki.streaming import StreamEvent, parse_stream_json

__all__ = [
    "AsyncioSpawner",
    "LockAcquirer",
    "Spawner",
    "StreamEvent",
    "WikiLockAdapter",
    "WikiRunnerError",
    "WikiRunnerTimeoutError",
    "parse_stream_json",
    "run_wiki_session",
]
