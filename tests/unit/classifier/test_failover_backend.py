from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import ai_steward_wiki.classifier as classifier_package
from ai_steward_wiki.classifier.backend import FailoverClassifierBackend
from ai_steward_wiki.classifier.schema import ClassifierError
from ai_steward_wiki.llm.codex import CodexRequest, CodexRunKind
from ai_steward_wiki.llm.failover import (
    AttemptEvidence,
    EvidenceKind,
    FailoverPolicy,
    ProviderLimitError,
    ProviderState,
)


class StubPrimary:
    name = "claude_cli"
    model = "claude-haiku-4-5"

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or {}
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def call(
        self,
        *,
        text: str,
        prompt_path: Path,
        correlation_id: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "text": text,
                "prompt_path": prompt_path,
                "correlation_id": correlation_id,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


class StubCodex:
    light_model = "gpt-5.4-mini"
    light_reasoning = "low"
    neutral_cwd = Path("/tmp/codex-runtime")

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[CodexRequest] = []

    async def run_structured(self, request: CodexRequest) -> dict[str, Any]:
        self.calls.append(request)
        return self.result


def typed_limit() -> ProviderLimitError:
    return ProviderLimitError(
        provider="claude",
        reset_at=None,
        evidence=AttemptEvidence(EvidenceKind.READ_ONLY),
    )


def prompt_file(tmp_path: Path) -> Path:
    path = tmp_path / "classifier.md"
    path.write_text("SYSTEM CLASSIFIER", encoding="utf-8")
    return path


def test_classifier_package_exports_failover_backend() -> None:
    assert classifier_package.FailoverClassifierBackend is FailoverClassifierBackend


async def test_healthy_claude_never_calls_codex(tmp_path: Path) -> None:
    primary = StubPrimary(result={"intent": "WIKI_QUERY"})
    codex = StubCodex({"intent": "unused"})
    backend = FailoverClassifierBackend(
        primary=primary,
        codex=codex,  # type: ignore[arg-type]
        policy=FailoverPolicy(cooldown_s=900.0),
        timeout_s=30.0,
    )

    result = await backend.call(
        text="query",
        prompt_path=prompt_file(tmp_path),
        correlation_id="corr-1",
    )

    assert result == {"intent": "WIKI_QUERY"}
    assert codex.calls == []
    assert backend.name == "claude_cli"
    assert backend.model == "claude-haiku-4-5"


async def test_typed_limit_maps_to_light_codex_profile(tmp_path: Path) -> None:
    primary = StubPrimary(error=typed_limit())
    codex = StubCodex({"intent": "WIKI_QUERY", "confidence": 1.0})
    policy = FailoverPolicy(cooldown_s=900.0)
    backend = FailoverClassifierBackend(
        primary=primary,
        codex=codex,  # type: ignore[arg-type]
        policy=policy,
        timeout_s=30.0,
    )

    result = await backend.call(
        text="query",
        prompt_path=prompt_file(tmp_path),
        correlation_id="corr-2",
    )

    assert result["intent"] == "WIKI_QUERY"
    request = codex.calls[0]
    assert request.model == "gpt-5.4-mini"
    assert request.reasoning == "low"
    assert request.run_kind is CodexRunKind.STRUCTURED
    assert request.output_schema == {"type": "object", "additionalProperties": True}
    assert "SYSTEM CLASSIFIER" in request.prompt
    assert "query" in request.prompt
    assert policy.state is ProviderState.CODEX


async def test_generic_classifier_error_does_not_fallback(tmp_path: Path) -> None:
    primary = StubPrimary(error=ClassifierError("generic"))
    codex = StubCodex({"intent": "unused"})
    backend = FailoverClassifierBackend(
        primary=primary,
        codex=codex,  # type: ignore[arg-type]
        policy=FailoverPolicy(cooldown_s=900.0),
        timeout_s=30.0,
    )

    with pytest.raises(ClassifierError, match="generic"):
        await backend.call(
            text="query",
            prompt_path=prompt_file(tmp_path),
            correlation_id="corr-3",
        )

    assert codex.calls == []
