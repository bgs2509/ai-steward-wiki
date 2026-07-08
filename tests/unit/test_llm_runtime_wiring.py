from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from structlog.testing import capture_logs

from ai_steward_wiki import __main__ as runtime
from ai_steward_wiki.llm.codex import CodexCliAdapter, CodexReadiness
from ai_steward_wiki.settings import Settings


def _settings(tmp_path: Path, *, enabled: bool = True) -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            llm_codex_enabled=enabled,
            llm_failover_cooldown_s=120.0,
            codex_cli_binary="codex",
            codex_cli_version="0.142.5",
            codex_home=tmp_path / "codex-home",
            codex_light_model="gpt-5.4-mini",
            codex_light_reasoning="low",
            codex_complex_model="gpt-5.5",
            codex_complex_reasoning="medium",
            workspace_root=tmp_path / "workspace",
            wiki_runner_model="claude-sonnet-4-5",
            wiki_runner_timeout_s=300.0,
            wiki_runner_term_grace_s=10.0,
            stage0_backend="claude_cli",
            stage0_api_credential_path=None,
            classifier_stage0_timeout_s=30.0,
            prompts_dir=tmp_path / "prompts",
        ),
    )


async def test_ready_codex_builds_one_shared_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    adapter = MagicMock(spec=CodexCliAdapter)
    adapter.check_readiness = AsyncMock(
        return_value=CodexReadiness(True, None, "/usr/bin/codex", "0.142.5")
    )
    monkeypatch.setattr(runtime, "_make_codex_adapter", lambda _settings: adapter, raising=False)

    llm_runtime = await runtime._build_llm_runtime(settings)

    assert llm_runtime.codex is adapter
    config = runtime._wiki_run_config(settings=settings, llm_runtime=llm_runtime)
    assert config.failover_policy is llm_runtime.policy
    assert config.codex_adapter is adapter


async def test_ready_codex_logs_positive_readiness_anchor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    adapter = MagicMock(spec=CodexCliAdapter)
    adapter.check_readiness = AsyncMock(
        return_value=CodexReadiness(True, None, "/usr/local/bin/codex", "0.142.5")
    )
    monkeypatch.setattr(runtime, "_make_codex_adapter", lambda _settings: adapter, raising=False)

    with capture_logs() as records:
        llm_runtime = await runtime._build_llm_runtime(settings)

    assert llm_runtime.codex is adapter
    ready_events = [record for record in records if record["event"] == "llm.provider.ready"]
    assert len(ready_events) == 1
    assert ready_events[0]["provider"] == "codex"
    assert ready_events[0]["run_kind"] == "readiness"
    assert ready_events[0]["correlation_id"] == "startup"
    assert ready_events[0]["outcome"] == "fallback_enabled"


async def test_failed_codex_readiness_keeps_claude_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    adapter = MagicMock(spec=CodexCliAdapter)
    adapter.check_readiness = AsyncMock(
        return_value=CodexReadiness(
            False,
            "authentication_unavailable",
            "/usr/bin/codex",
            "0.142.5",
        )
    )
    monkeypatch.setattr(runtime, "_make_codex_adapter", lambda _settings: adapter, raising=False)

    llm_runtime = await runtime._build_llm_runtime(settings)

    assert llm_runtime.codex is None
    config = runtime._wiki_run_config(settings=settings, llm_runtime=llm_runtime)
    assert config.failover_policy is None
    assert config.codex_adapter is None


async def test_disabled_codex_skips_readiness(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, enabled=False)
    factory = MagicMock()
    monkeypatch.setattr(runtime, "_make_codex_adapter", factory, raising=False)

    llm_runtime = await runtime._build_llm_runtime(settings)

    assert llm_runtime.codex is None
    factory.assert_not_called()


async def test_codex_factory_failure_keeps_claude_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)

    def failed_factory(_settings: Settings) -> CodexCliAdapter:
        raise OSError("codex setup failed")

    monkeypatch.setattr(runtime, "_make_codex_adapter", failed_factory)

    llm_runtime = await runtime._build_llm_runtime(settings)

    assert llm_runtime.codex is None


async def test_classifier_uses_shared_policy_when_codex_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    adapter = MagicMock(spec=CodexCliAdapter)
    adapter.check_readiness = AsyncMock(
        return_value=CodexReadiness(True, None, "/usr/bin/codex", "0.142.5")
    )
    monkeypatch.setattr(runtime, "_make_codex_adapter", lambda _settings: adapter, raising=False)
    llm_runtime = await runtime._build_llm_runtime(settings)

    backend = runtime._build_classifier_backend(settings, llm_runtime)

    assert backend.policy is llm_runtime.policy
    assert backend.codex is adapter


async def test_schema_generator_uses_shared_policy_when_codex_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    adapter = MagicMock(spec=CodexCliAdapter)
    adapter.check_readiness = AsyncMock(
        return_value=CodexReadiness(True, None, "/usr/bin/codex", "0.142.5")
    )
    monkeypatch.setattr(runtime, "_make_codex_adapter", lambda _settings: adapter)
    llm_runtime = await runtime._build_llm_runtime(settings)

    generator = runtime._build_schema_generator(settings, llm_runtime)

    assert generator.policy is llm_runtime.policy
    assert generator.codex is adapter
