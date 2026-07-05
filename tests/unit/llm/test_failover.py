from __future__ import annotations

import asyncio
import statistics
import time
from datetime import UTC, datetime, timedelta

import pytest

import ai_steward_wiki.llm as llm_package
from ai_steward_wiki.llm.failover import (
    AttemptEvidence,
    EvidenceKind,
    FailoverEvent,
    FailoverPolicy,
    ProviderLimitError,
    ProviderState,
    ProvidersUnavailableError,
    ReplayBlockedError,
)


class FakeClock:
    def __init__(self) -> None:
        self.monotonic = 100.0
        self.now = datetime(2026, 7, 5, 12, tzinfo=UTC)

    def tick(self) -> float:
        return self.monotonic

    def utcnow(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.monotonic += seconds
        self.now += timedelta(seconds=seconds)


def limit_error(
    *,
    evidence: EvidenceKind = EvidenceKind.READ_ONLY,
    reset_at: datetime | None = None,
) -> ProviderLimitError:
    return ProviderLimitError(
        provider="claude",
        reset_at=reset_at,
        evidence=AttemptEvidence(evidence, reason=evidence.value),
    )


def policy(clock: FakeClock, events: list[FailoverEvent] | None = None) -> FailoverPolicy:
    return FailoverPolicy(
        cooldown_s=900.0,
        clock=clock.tick,
        utcnow=clock.utcnow,
        on_event=None if events is None else events.append,
    )


def test_provider_state_uses_approved_names() -> None:
    assert [state.value for state in ProviderState] == ["claude", "codex", "probe"]


def test_only_read_only_evidence_is_replay_safe() -> None:
    assert AttemptEvidence(EvidenceKind.READ_ONLY).replay_safe is True
    assert AttemptEvidence(EvidenceKind.MUTATION).replay_safe is False
    assert AttemptEvidence(EvidenceKind.DELIVERED).replay_safe is False
    assert AttemptEvidence(EvidenceKind.UNKNOWN).replay_safe is False


def test_llm_package_exports_failover_contract() -> None:
    assert llm_package.FailoverPolicy is FailoverPolicy
    assert llm_package.ProviderState is ProviderState
    assert llm_package.ProviderLimitError is ProviderLimitError


async def test_healthy_claude_stays_primary() -> None:
    clock = FakeClock()
    failover = policy(clock)
    calls: list[str] = []

    async def claude() -> str:
        calls.append("claude")
        return "primary"

    async def codex() -> str:
        calls.append("codex")
        return "fallback"

    result = await failover.execute(
        run_kind="structured",
        correlation_id="corr-1",
        claude=claude,
        codex=codex,
    )

    assert result == "primary"
    assert calls == ["claude"]
    assert failover.state is ProviderState.CLAUDE
    assert failover.metrics.primary_success == 1


async def test_generic_claude_failure_does_not_fallback() -> None:
    clock = FakeClock()
    failover = policy(clock)
    codex_calls = 0

    async def claude() -> str:
        raise RuntimeError("generic failure")

    async def codex() -> str:
        nonlocal codex_calls
        codex_calls += 1
        return "fallback"

    with pytest.raises(RuntimeError, match="generic failure"):
        await failover.execute(
            run_kind="structured",
            correlation_id="corr-2",
            claude=claude,
            codex=codex,
        )

    assert codex_calls == 0
    assert failover.state is ProviderState.CLAUDE


async def test_safe_limit_moves_to_codex_and_falls_back() -> None:
    clock = FakeClock()
    events: list[FailoverEvent] = []
    failover = policy(clock, events)

    async def claude() -> str:
        raise limit_error()

    async def codex() -> str:
        return "fallback"

    result = await failover.execute(
        run_kind="structured",
        correlation_id="corr-3",
        claude=claude,
        codex=codex,
    )

    assert result == "fallback"
    assert failover.state is ProviderState.CODEX
    assert failover.metrics.fallback_success == 1
    assert "llm.failover.triggered" in [event.event for event in events]
    assert "llm.circuit.changed" in [event.event for event in events]


@pytest.mark.parametrize(
    "kind",
    [EvidenceKind.MUTATION, EvidenceKind.DELIVERED, EvidenceKind.UNKNOWN],
)
async def test_unsafe_limit_blocks_replay_but_opens_codex_state(kind: EvidenceKind) -> None:
    clock = FakeClock()
    failover = policy(clock)
    codex_calls = 0

    async def claude() -> str:
        raise limit_error(evidence=kind)

    async def codex() -> str:
        nonlocal codex_calls
        codex_calls += 1
        return "fallback"

    with pytest.raises(ReplayBlockedError):
        await failover.execute(
            run_kind="agent_write",
            correlation_id="corr-4",
            claude=claude,
            codex=codex,
        )

    assert codex_calls == 0
    assert failover.state is ProviderState.CODEX
    assert failover.metrics.blocked_replay == 1


async def test_subsequent_request_skips_claude_until_probe_window() -> None:
    clock = FakeClock()
    failover = policy(clock)
    claude_calls = 0
    codex_calls = 0

    async def limited_claude() -> str:
        raise limit_error()

    async def codex() -> str:
        nonlocal codex_calls
        codex_calls += 1
        return "fallback"

    await failover.execute(
        run_kind="text",
        correlation_id="corr-5a",
        claude=limited_claude,
        codex=codex,
    )

    async def healthy_claude() -> str:
        nonlocal claude_calls
        claude_calls += 1
        return "primary"

    result = await failover.execute(
        run_kind="text",
        correlation_id="corr-5b",
        claude=healthy_claude,
        codex=codex,
    )

    assert result == "fallback"
    assert claude_calls == 0
    assert codex_calls == 2


async def test_reported_reset_time_controls_probe_window() -> None:
    clock = FakeClock()
    failover = policy(clock)
    reset_at = clock.now + timedelta(seconds=120)

    async def limited_claude() -> str:
        raise limit_error(reset_at=reset_at)

    async def codex() -> str:
        return "fallback"

    await failover.execute(
        run_kind="text",
        correlation_id="corr-6a",
        claude=limited_claude,
        codex=codex,
    )
    assert failover.seconds_until_probe == pytest.approx(120.0)

    clock.advance(119.0)
    assert failover.seconds_until_probe == pytest.approx(1.0)


async def test_successful_single_probe_restores_claude() -> None:
    clock = FakeClock()
    failover = policy(clock)

    async def limited_claude() -> str:
        raise limit_error()

    async def codex() -> str:
        return "fallback"

    await failover.execute(
        run_kind="text",
        correlation_id="corr-7a",
        claude=limited_claude,
        codex=codex,
    )
    clock.advance(900.0)

    async def recovered_claude() -> str:
        return "recovered"

    result = await failover.execute(
        run_kind="text",
        correlation_id="corr-7b",
        claude=recovered_claude,
        codex=codex,
    )

    assert result == "recovered"
    assert failover.state is ProviderState.CLAUDE
    assert failover.metrics.automatic_failback == 1


async def test_concurrent_requests_start_only_one_probe() -> None:
    clock = FakeClock()
    failover = policy(clock)
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()
    claude_calls = 0

    async def limited_claude() -> str:
        raise limit_error()

    async def codex() -> str:
        return "codex"

    await failover.execute(
        run_kind="text",
        correlation_id="corr-8a",
        claude=limited_claude,
        codex=codex,
    )
    clock.advance(900.0)

    async def probe_claude() -> str:
        nonlocal claude_calls
        claude_calls += 1
        probe_started.set()
        await release_probe.wait()
        return "probe"

    first = asyncio.create_task(
        failover.execute(
            run_kind="text",
            correlation_id="corr-8b",
            claude=probe_claude,
            codex=codex,
        )
    )
    await probe_started.wait()
    others = [
        asyncio.create_task(
            failover.execute(
                run_kind="text",
                correlation_id=f"corr-8-{index}",
                claude=probe_claude,
                codex=codex,
            )
        )
        for index in range(9)
    ]
    assert await asyncio.gather(*others) == ["codex"] * 9
    release_probe.set()
    assert await first == "probe"
    assert claude_calls == 1


async def test_generic_probe_failure_returns_to_codex() -> None:
    clock = FakeClock()
    failover = policy(clock)

    async def limited_claude() -> str:
        raise limit_error()

    async def codex() -> str:
        return "codex"

    await failover.execute(
        run_kind="text",
        correlation_id="corr-9a",
        claude=limited_claude,
        codex=codex,
    )
    clock.advance(900.0)

    async def failed_probe() -> str:
        raise RuntimeError("probe transport")

    result = await failover.execute(
        run_kind="text",
        correlation_id="corr-9b",
        claude=failed_probe,
        codex=codex,
    )

    assert result == "codex"
    assert failover.state is ProviderState.CODEX
    assert failover.seconds_until_probe == pytest.approx(900.0)


async def test_codex_failure_is_typed_as_dual_provider_failure() -> None:
    clock = FakeClock()
    failover = policy(clock)

    async def limited_claude() -> str:
        raise limit_error()

    async def failed_codex() -> str:
        raise RuntimeError("codex unavailable")

    with pytest.raises(ProvidersUnavailableError) as captured:
        await failover.execute(
            run_kind="text",
            correlation_id="corr-10",
            claude=limited_claude,
            codex=failed_codex,
        )

    assert isinstance(captured.value.primary_error, ProviderLimitError)
    assert str(captured.value.fallback_error) == "codex unavailable"
    assert failover.metrics.both_provider_failure == 1


async def test_selection_overhead_p95_is_below_100_ms() -> None:
    clock = FakeClock()
    failover = policy(clock)

    async def claude() -> str:
        return "ok"

    async def codex() -> str:
        return "unused"

    durations_ms: list[float] = []
    for index in range(200):
        started = time.perf_counter()
        await failover.execute(
            run_kind="structured",
            correlation_id=f"perf-{index}",
            claude=claude,
            codex=codex,
        )
        durations_ms.append((time.perf_counter() - started) * 1000)

    p95 = statistics.quantiles(durations_ms, n=20)[18]
    assert p95 < 100.0
