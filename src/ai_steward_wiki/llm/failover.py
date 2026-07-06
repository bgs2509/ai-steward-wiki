# FILE: src/ai_steward_wiki/llm/failover.py
# VERSION: 0.2.0
# START_MODULE_CONTRACT
#   PURPOSE: Select Claude, Codex, or one Claude probe and permit fallback only for typed limits and proven-safe replay.
#   SCOPE: ProviderState; ProviderLimitError; AttemptEvidence; process-local circuit;
#          single-flight probe; generic typed execution policy; structured transition logs.
#   DEPENDS: asyncio, dataclasses, datetime, enum, typing,
#            ai_steward_wiki.logging_events
#   LINKS: M-LLM-FAILOVER, M-LLM-CODEX, ADR-035, aisw-8gw, FR-1, FR-3, FR-7, FR-8, FR-9, FR-10
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ProviderState - process-local claude, codex, and probe states
#   EvidenceKind - read-only, mutation, delivered, and unknown effect categories
#   AttemptEvidence - fail-closed replay evidence for one provider attempt
#   ProviderLimitError - typed provider limit with optional reset and evidence
#   ReplayBlockedError - unsafe same-operation fallback rejection
#   ProvidersUnavailableError - primary and fallback failure pair
#   FailoverEvent - sanitized provider decision event
#   FailoverMetrics - in-process outcome counters
#   FailoverPolicy - atomic selection, transition, probe, and replay orchestration
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 - aisw-8gw: use the stable LLM event catalog and expose
#                an optional sanitized model field for runtime telemetry.
#   PREVIOUS:    v0.1.0 - aisw-8gw: implement process-local circuit, typed limits,
#                replay guard, single-flight probe, events, and outcome counters.
#   PREVIOUS:    v0.0.0 - aisw-8gw: contract-only planning stub.
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

from ai_steward_wiki.logging_events import (
    LLM_CIRCUIT_CHANGED,
    LLM_FAILOVER_TRIGGERED,
    LLM_PROVIDER_FAILED,
    LLM_PROVIDER_RECOVERED,
    LLM_PROVIDER_SELECTED,
    LLM_REPLAY_BLOCKED,
)

__all__ = [
    "AttemptEvidence",
    "EvidenceKind",
    "FailoverEvent",
    "FailoverMetrics",
    "FailoverPolicy",
    "ProviderLimitError",
    "ProviderState",
    "ProvidersUnavailableError",
    "ReplayBlockedError",
]

T = TypeVar("T")
Attempt = Callable[[], Awaitable[T]]
EventSink = Callable[["FailoverEvent"], None]


class ProviderState(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    PROBE = "probe"


class EvidenceKind(StrEnum):
    READ_ONLY = "read_only"
    MUTATION = "mutation"
    DELIVERED = "delivered"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AttemptEvidence:
    kind: EvidenceKind
    reason: str | None = None

    @property
    def replay_safe(self) -> bool:
        return self.kind is EvidenceKind.READ_ONLY


class ProviderLimitError(RuntimeError):
    def __init__(
        self,
        *,
        provider: str,
        reset_at: datetime | None,
        evidence: AttemptEvidence,
    ) -> None:
        super().__init__(f"{provider} subscription limit")
        self.provider = provider
        self.reset_at = reset_at
        self.evidence = evidence


class ReplayBlockedError(RuntimeError):
    def __init__(self, evidence: AttemptEvidence) -> None:
        super().__init__(evidence.reason or evidence.kind.value)
        self.evidence = evidence


class ProvidersUnavailableError(RuntimeError):
    def __init__(self, primary_error: Exception, fallback_error: Exception) -> None:
        super().__init__("primary and fallback providers are unavailable")
        self.primary_error = primary_error
        self.fallback_error = fallback_error


@dataclass(frozen=True, slots=True)
class FailoverEvent:
    event: str
    provider: str
    run_kind: str
    correlation_id: str
    outcome: str
    model: str | None = None
    previous_state: str | None = None
    next_state: str | None = None
    reason: str | None = None
    evidence: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FailoverMetrics:
    primary_success: int = 0
    fallback_success: int = 0
    both_provider_failure: int = 0
    blocked_replay: int = 0
    automatic_failback: int = 0


@dataclass(frozen=True, slots=True)
class _Selection:
    provider: ProviderState
    is_probe: bool = False


class FailoverPolicy:
    """Coordinate Claude-first execution with safe Codex fallback."""

    def __init__(
        self,
        *,
        cooldown_s: float,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_event: EventSink | None = None,
    ) -> None:
        if cooldown_s <= 0:
            raise ValueError("cooldown_s must be positive")
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._utcnow = utcnow
        self._on_event = on_event
        self._lock = asyncio.Lock()
        self._state = ProviderState.CLAUDE
        self._probe_after = 0.0
        self._last_limit_error: ProviderLimitError | None = None
        self._metrics = FailoverMetrics()

    @property
    def state(self) -> ProviderState:
        return self._state

    @property
    def seconds_until_probe(self) -> float:
        if self._state is ProviderState.CLAUDE:
            return 0.0
        return max(0.0, self._probe_after - self._clock())

    @property
    def metrics(self) -> FailoverMetrics:
        return self._metrics

    # START_CONTRACT: execute
    #   PURPOSE: Run Claude first, then Codex only after a typed safe limit.
    #   INPUTS: run_kind, correlation_id, claude attempt, codex attempt
    #   OUTPUTS: successful result from exactly one provider
    #   SIDE_EFFECTS: updates process-local circuit, metrics, and event sink
    #   LINKS: M-LLM-FAILOVER, DF-LLM-SAFE-FAILOVER, DF-LLM-CIRCUIT-RECOVERY
    # END_CONTRACT: execute
    async def execute(
        self,
        *,
        run_kind: str,
        correlation_id: str,
        claude: Attempt[T],
        codex: Attempt[T],
    ) -> T:
        # START_BLOCK_SELECT_PROVIDER
        selection = await self._select(run_kind, correlation_id)
        # END_BLOCK_SELECT_PROVIDER
        if selection.provider is ProviderState.CODEX:
            return await self._run_codex(codex, run_kind, correlation_id)

        started = time.perf_counter_ns()
        try:
            result = await claude()
        except ProviderLimitError as error:
            await self._move_to_codex(error, run_kind, correlation_id)
            self._emit(
                FailoverEvent(
                    event=LLM_FAILOVER_TRIGGERED,
                    provider="claude",
                    run_kind=run_kind,
                    correlation_id=correlation_id,
                    outcome="limit",
                    reason="subscription_limit",
                    evidence=error.evidence.kind.value,
                    latency_ms=self._elapsed_ms(started),
                )
            )
            # START_BLOCK_REPLAY_GUARD
            if not error.evidence.replay_safe:
                self._increment("blocked_replay")
                self._emit(
                    FailoverEvent(
                        event=LLM_REPLAY_BLOCKED,
                        provider="claude",
                        run_kind=run_kind,
                        correlation_id=correlation_id,
                        outcome="blocked",
                        reason=error.evidence.reason,
                        evidence=error.evidence.kind.value,
                    )
                )
                raise ReplayBlockedError(error.evidence) from error
            # END_BLOCK_REPLAY_GUARD
            return await self._run_codex(codex, run_kind, correlation_id)
        except Exception:
            if selection.is_probe:
                await self._defer_probe(run_kind, correlation_id)
                return await self._run_codex(codex, run_kind, correlation_id)
            raise

        self._increment("primary_success")
        if selection.is_probe:
            await self._recover_claude(run_kind, correlation_id)
        return result

    async def _select(self, run_kind: str, correlation_id: str) -> _Selection:
        transition: tuple[ProviderState, ProviderState] | None = None
        async with self._lock:
            if self._state is ProviderState.CLAUDE:
                selection = _Selection(ProviderState.CLAUDE)
            elif self._state is ProviderState.CODEX and self._clock() >= self._probe_after:
                transition = (self._state, ProviderState.PROBE)
                self._state = ProviderState.PROBE
                selection = _Selection(ProviderState.CLAUDE, is_probe=True)
            else:
                selection = _Selection(ProviderState.CODEX)
        if transition is not None:
            self._emit_transition(*transition, run_kind, correlation_id, "probe_window")
        self._emit(
            FailoverEvent(
                event=LLM_PROVIDER_SELECTED,
                provider=selection.provider.value,
                run_kind=run_kind,
                correlation_id=correlation_id,
                outcome="probe" if selection.is_probe else "selected",
            )
        )
        return selection

    async def _move_to_codex(
        self,
        error: ProviderLimitError,
        run_kind: str,
        correlation_id: str,
    ) -> None:
        async with self._lock:
            previous = self._state
            self._state = ProviderState.CODEX
            self._last_limit_error = error
            self._probe_after = self._clock() + self._probe_delay(error.reset_at)
        self._emit_transition(previous, ProviderState.CODEX, run_kind, correlation_id, "limit")

    async def _defer_probe(self, run_kind: str, correlation_id: str) -> None:
        async with self._lock:
            previous = self._state
            self._state = ProviderState.CODEX
            self._probe_after = self._clock() + self._cooldown_s
        self._emit_transition(
            previous,
            ProviderState.CODEX,
            run_kind,
            correlation_id,
            "probe_failed",
        )

    async def _recover_claude(self, run_kind: str, correlation_id: str) -> None:
        async with self._lock:
            previous = self._state
            self._state = ProviderState.CLAUDE
            self._probe_after = 0.0
            self._last_limit_error = None
        self._increment("automatic_failback")
        self._emit_transition(
            previous,
            ProviderState.CLAUDE,
            run_kind,
            correlation_id,
            "probe_success",
        )
        self._emit(
            FailoverEvent(
                event=LLM_PROVIDER_RECOVERED,
                provider="claude",
                run_kind=run_kind,
                correlation_id=correlation_id,
                outcome="recovered",
            )
        )

    async def _run_codex(
        self,
        codex: Attempt[T],
        run_kind: str,
        correlation_id: str,
    ) -> T:
        started = time.perf_counter_ns()
        try:
            result = await codex()
        except Exception as fallback_error:
            self._increment("both_provider_failure")
            self._emit(
                FailoverEvent(
                    event=LLM_PROVIDER_FAILED,
                    provider="codex",
                    run_kind=run_kind,
                    correlation_id=correlation_id,
                    outcome="failed",
                    reason=type(fallback_error).__name__,
                    latency_ms=self._elapsed_ms(started),
                )
            )
            primary_error = self._last_limit_error or RuntimeError("claude unavailable")
            raise ProvidersUnavailableError(primary_error, fallback_error) from fallback_error
        self._increment("fallback_success")
        return result

    def _probe_delay(self, reset_at: datetime | None) -> float:
        if reset_at is None or reset_at.tzinfo is None:
            return self._cooldown_s
        now = self._utcnow()
        if now.tzinfo is None:
            return self._cooldown_s
        return max(0.0, (reset_at - now).total_seconds())

    def _increment(self, field: str) -> None:
        values = {
            "primary_success": self._metrics.primary_success,
            "fallback_success": self._metrics.fallback_success,
            "both_provider_failure": self._metrics.both_provider_failure,
            "blocked_replay": self._metrics.blocked_replay,
            "automatic_failback": self._metrics.automatic_failback,
        }
        values[field] += 1
        self._metrics = FailoverMetrics(**values)

    def _emit_transition(
        self,
        previous: ProviderState,
        next_state: ProviderState,
        run_kind: str,
        correlation_id: str,
        reason: str,
    ) -> None:
        self._emit(
            FailoverEvent(
                event=LLM_CIRCUIT_CHANGED,
                provider=next_state.value,
                run_kind=run_kind,
                correlation_id=correlation_id,
                outcome="changed",
                previous_state=previous.value,
                next_state=next_state.value,
                reason=reason,
            )
        )

    def _emit(self, event: FailoverEvent) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception:
            return

    @staticmethod
    def _elapsed_ms(started_ns: int) -> int:
        return int((time.perf_counter_ns() - started_ns) / 1_000_000)
