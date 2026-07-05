# FILE: src/ai_steward_wiki/wiki/schema_gen.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Generate a tailored Karpathy schema (CLAUDE.md managed zone) for an
#            arbitrary-domain WIKI at create time, when no static preset matches.
#   SCOPE: SchemaGenerator Protocol + Claude/Codex failover impl + Fake; validate_schema;
#          apply_generated_schema orchestration (generate -> validate -> write via
#          repair_managed_zone with template_id=_generated, never clobbered later).
#   DEPENDS: ai_steward_wiki.classifier.backend (Spawner seam),
#            ai_steward_wiki.claude_cli.common, ai_steward_wiki.llm.{failover,codex},
#            ai_steward_wiki.wiki.migration
#   LINKS: M-WIKI-LIFECYCLE, M-WIKI-MIGRATION, M-LLM-FAILOVER, M-LLM-CODEX,
#          aisw-b50, aisw-8gw, D-017 (Variant D), D-039
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   GENERATED_TEMPLATE_ID - "_generated" sentinel template_id for LLM-authored schemas
#   REQUIRED_SECTIONS - markdown headers a valid generated schema must contain
#   SchemaGenError - generation failure (non-zero CLI rc / empty output)
#   SchemaGenerator - Protocol; async generate(wiki_name, first_content, correlation_id) -> str
#   ClaudeCliSchemaGenerator - default impl spawning `claude -p` with prompts/schema-gen.md
#   FailoverSchemaGenerator - Claude-first text generator with safe gpt-5.5 fallback
#   FakeSchemaGenerator - test double returning canned markdown
#   validate_schema - structural check: required sections + >=1 topic-specific section
#   apply_generated_schema - generate+validate+write managed zone; True if applied, False on fallback
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-8gw: unwrap Claude JSON envelopes and add safe
#                gpt-5.5 medium text fallback before managed-zone validation and write.
#   PREVIOUS:    v0.0.2 - aisw-8gw: contract-only plan for safe Codex text fallback.
#   PREVIOUS:    v0.0.1 - aisw-b50: arbitrary-domain schema generation at create.
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import structlog

from ai_steward_wiki.classifier.backend import AsyncioSpawner, Spawner
from ai_steward_wiki.claude_cli.common import (
    build_env,
    neutral_cwd,
    parse_claude_subscription_limit,
    resolve_binary,
    system_prompt_argv,
    truncate_stderr,
)
from ai_steward_wiki.llm.codex import CodexCliAdapter, CodexRequest, CodexRunKind
from ai_steward_wiki.llm.failover import (
    AttemptEvidence,
    EvidenceKind,
    FailoverPolicy,
    ProviderLimitError,
)
from ai_steward_wiki.wiki.migration import repair_managed_zone

__all__ = [
    "GENERATED_TEMPLATE_ID",
    "REQUIRED_SECTIONS",
    "ClaudeCliSchemaGenerator",
    "FailoverSchemaGenerator",
    "FakeSchemaGenerator",
    "SchemaGenError",
    "SchemaGenerator",
    "apply_generated_schema",
    "validate_schema",
]

_log = structlog.get_logger("wiki.schema_gen")

GENERATED_TEMPLATE_ID = "_generated"
REQUIRED_SECTIONS = ("## Data layout", "## File resolution", "## Inbox hint")


class SchemaGenError(RuntimeError):
    """Raised when the schema generator subprocess fails or returns nothing."""


@runtime_checkable
class SchemaGenerator(Protocol):
    async def generate(self, *, wiki_name: str, first_content: str, correlation_id: str) -> str: ...


def validate_schema(managed: str) -> bool:
    """True if the generated managed text is structurally usable.

    Requires every section in REQUIRED_SECTIONS plus at least one *topic-specific*
    `## ` header beyond them (the whole point of generation — domain sections such
    as `## Персонажи` for an anime WIKI). Cheap structural gate, not semantic.
    """
    if not all(section in managed for section in REQUIRED_SECTIONS):
        return False
    headers = [line.strip() for line in managed.splitlines() if line.startswith("## ")]
    topic_headers = [h for h in headers if h not in REQUIRED_SECTIONS]
    return len(topic_headers) >= 1


@dataclass
class ClaudeCliSchemaGenerator:
    """Default generator: one `claude -p` text turn with prompts/schema-gen.md.

    No tools, no settings discovery, dontAsk — a pure text completion that returns
    a CLAUDE.md managed-zone body. Subprocess sits behind the Spawner seam (shared
    with the Stage-0 classifier) so tests inject a fake without touching this class.
    """

    claude_config_dir: Path
    prompt_path: Path
    timeout_s: float = 60.0
    binary: str = "claude"
    model: str = "claude-sonnet-4-5"
    spawner: Spawner = field(default_factory=AsyncioSpawner)

    def _argv(self) -> list[str]:
        return [
            self.binary,
            "-p",
            "--model",
            self.model,
            "--output-format",
            "json",
            "--max-turns",
            "1",
            *system_prompt_argv(self.prompt_path),
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
        ]

    async def generate(self, *, wiki_name: str, first_content: str, correlation_id: str) -> str:
        argv = self._argv()
        argv[0] = resolve_binary(self.binary)
        stdin = (
            f"WIKI name: {wiki_name}\n" f"First content from the user:\n{first_content}\n"
        ).encode()
        rc, stdout, stderr = await self.spawner.spawn(
            argv,
            env=build_env(self.claude_config_dir),
            stdin=stdin,
            timeout_s=self.timeout_s,
            cwd=str(neutral_cwd(self.claude_config_dir)),
        )
        try:
            envelope = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if rc != 0:
                raise SchemaGenError(
                    f"schema-gen CLI rc={rc}; stderr={truncate_stderr(stderr)}"
                ) from exc
            raise SchemaGenError("schema-gen CLI returned invalid JSON") from exc
        if not isinstance(envelope, dict):
            raise SchemaGenError("schema-gen CLI JSON is not an object")
        limit = parse_claude_subscription_limit(envelope)
        if limit is not None:
            raise ProviderLimitError(
                provider="claude",
                reset_at=limit.reset_at,
                evidence=AttemptEvidence(EvidenceKind.READ_ONLY, "schema generation"),
            )
        if rc != 0:
            raise SchemaGenError(f"schema-gen CLI rc={rc}; stderr={truncate_stderr(stderr)}")
        result = envelope.get("result")
        if envelope.get("subtype") != "success" or not isinstance(result, str):
            raise SchemaGenError("schema-gen CLI envelope is not successful text")
        text = result.strip()
        if not text:
            raise SchemaGenError("schema-gen CLI returned empty output")
        return text


@dataclass
class FailoverSchemaGenerator:
    """Generate text through Claude first and Codex only after a typed safe limit."""

    primary: SchemaGenerator
    codex: CodexCliAdapter
    policy: FailoverPolicy
    prompt_path: Path
    timeout_s: float

    async def generate(self, *, wiki_name: str, first_content: str, correlation_id: str) -> str:
        system_prompt = self.prompt_path.read_text(encoding="utf-8")
        return await self.policy.execute(
            run_kind="text",
            correlation_id=correlation_id,
            claude=lambda: self.primary.generate(
                wiki_name=wiki_name,
                first_content=first_content,
                correlation_id=correlation_id,
            ),
            codex=lambda: self.codex.run_text(
                CodexRequest(
                    prompt=(
                        f"{system_prompt}\n\n"
                        f"WIKI name: {wiki_name}\n"
                        "First content from the user:\n"
                        f"{first_content}\n"
                    ),
                    model=self.codex.complex_model,
                    reasoning=self.codex.complex_reasoning,
                    run_kind=CodexRunKind.TEXT,
                    correlation_id=correlation_id,
                    timeout_s=self.timeout_s,
                    cwd=self.codex.neutral_cwd,
                )
            ),
        )


@dataclass
class FakeSchemaGenerator:
    """Test double: returns canned markdown (or raises) without spawning anything."""

    canned: str = ""
    raises: Exception | None = None
    calls: list[dict[str, str]] = field(default_factory=list)

    async def generate(self, *, wiki_name: str, first_content: str, correlation_id: str) -> str:
        self.calls.append(
            {
                "wiki_name": wiki_name,
                "first_content": first_content,
                "correlation_id": correlation_id,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.canned


async def apply_generated_schema(
    *,
    claude_md: Path,
    wiki_name: str,
    first_content: str,
    correlation_id: str,
    generator: SchemaGenerator,
) -> bool:
    """Generate a tailored schema and write it into the WIKI's managed zone.

    Returns True when a validated schema was applied (template_id=_generated, so a
    later backfill/repair skips it and it co-evolves). Returns False on any failure
    or invalid output — the caller keeps the already-written `_default` schema as a
    safe fallback (Fail-Fast at the boundary, graceful degradation downstream).
    """
    try:
        managed = await generator.generate(
            wiki_name=wiki_name, first_content=first_content, correlation_id=correlation_id
        )
    except Exception as exc:
        _log.warning(
            "wiki.schema_gen.failed",
            correlation_id=correlation_id,
            wiki_name=wiki_name,
            error=type(exc).__name__,
        )
        return False

    managed = managed.strip()
    if not validate_schema(managed):
        _log.warning(
            "wiki.schema_gen.invalid",
            correlation_id=correlation_id,
            wiki_name=wiki_name,
            chars=len(managed),
        )
        return False

    sha = hashlib.sha256(managed.encode("utf-8")).hexdigest()
    repair_managed_zone(
        claude_md,
        template_managed=managed,
        template_sha256=sha,
        template_id=GENERATED_TEMPLATE_ID,
    )
    _log.info(
        "wiki.schema_gen.applied",
        correlation_id=correlation_id,
        wiki_name=wiki_name,
        chars=len(managed),
    )
    return True
