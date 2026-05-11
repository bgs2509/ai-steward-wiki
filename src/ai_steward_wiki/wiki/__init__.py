# FILE: src/ai_steward_wiki/wiki/__init__.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: WIKI package — Stage-1a/1b Sonnet runner (chunk 7) +
#            NL-driven lifecycle (chunk 8): naming, anti-spam, soft-delete,
#            frontmatter v1->v2 migration, 5-step pre-flight grounding.
#   SCOPE: package barrel; re-exports public API of submodules.
#   DEPENDS: ai_steward_wiki.scheduler (locks, kill-sequence), pydantic, structlog
#   LINKS: M-WIKI-RUNNER, M-WIKI-LIFECYCLE, D-007, D-008, D-011, D-012, D-021, D-039, D-041
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   StreamEvent - frozen Pydantic v2 model for one stream-json line
#   parse_stream_json - async iterator over StreamReader -> StreamEvent
#   LockAcquirer - Protocol seam for tests
#   WikiLockAdapter - default LockAcquirer wrapping scheduler.locks.WikiLockManager
#   Spawner - Protocol seam for subprocess (test seam)
#   AsyncioSpawner - default Spawner using asyncio.create_subprocess_exec
#   run_wiki_session - main Stage-1a/1b orchestrator
#   WikiRunnerError - base runner exception
#   WikiRunnerTimeoutError - timeout failure (after kill-sequence)
#   WikiName - frozen Pydantic (primary, hyphenated_lookup, slug)
#   WikiNameError - normalisation failure
#   normalize_wiki_name - NL string -> WikiName
#   WikiLifecycleManager - owner-scoped create/lookup/soft-delete/restore
#   TrashedWiki - soft-deleted wiki record
#   NearDuplicateMatch - Levenshtein <=2 match payload
#   AntiSpamCapError - cap-reached error
#   WikiNotFoundError - missing wiki on lookup
#   TrashRetentionExpiredError - restore beyond retention window
#   Frontmatter - CLAUDE.md frontmatter model
#   FrontmatterError - parse failure
#   parse_frontmatter - parse frontmatter from text
#   render_frontmatter - serialise frontmatter
#   extract_user_zone - extract user zone body
#   render_v2 - render full v2 CLAUDE.md
#   migrate_v1_to_v2 - atomic linear migration
#   PreflightCheck - single pre-flight check result
#   PreflightReport - aggregated pre-flight report
#   preflight - run 5-step pre-flight grounding
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - chunk 8: M-WIKI-LIFECYCLE (name/lifecycle/migration/preflight)
# END_CHANGE_SUMMARY

from ai_steward_wiki.wiki.acquire import LockAcquirer, WikiLockAdapter
from ai_steward_wiki.wiki.lifecycle import (
    AntiSpamCapError,
    NearDuplicateMatch,
    TrashedWiki,
    TrashRetentionExpiredError,
    WikiLifecycleManager,
    WikiNotFoundError,
)
from ai_steward_wiki.wiki.migration import (
    Frontmatter,
    FrontmatterError,
    extract_user_zone,
    migrate_v1_to_v2,
    parse_frontmatter,
    render_frontmatter,
    render_v2,
)
from ai_steward_wiki.wiki.name import WikiName, WikiNameError, normalize_wiki_name
from ai_steward_wiki.wiki.preflight import PreflightCheck, PreflightReport, preflight
from ai_steward_wiki.wiki.runner import (
    AsyncioSpawner,
    Spawner,
    WikiRunnerError,
    WikiRunnerTimeoutError,
    run_wiki_session,
)
from ai_steward_wiki.wiki.streaming import StreamEvent, parse_stream_json

__all__ = [
    "AntiSpamCapError",
    "AsyncioSpawner",
    "Frontmatter",
    "FrontmatterError",
    "LockAcquirer",
    "NearDuplicateMatch",
    "PreflightCheck",
    "PreflightReport",
    "Spawner",
    "StreamEvent",
    "TrashRetentionExpiredError",
    "TrashedWiki",
    "WikiLifecycleManager",
    "WikiLockAdapter",
    "WikiName",
    "WikiNameError",
    "WikiNotFoundError",
    "WikiRunnerError",
    "WikiRunnerTimeoutError",
    "extract_user_zone",
    "migrate_v1_to_v2",
    "normalize_wiki_name",
    "parse_frontmatter",
    "parse_stream_json",
    "preflight",
    "render_frontmatter",
    "render_v2",
    "run_wiki_session",
]
