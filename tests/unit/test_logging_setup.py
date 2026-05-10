from __future__ import annotations

import asyncio
import io
import json

import pytest
import structlog

from ai_steward_wiki.logging_setup import (
    bind_correlation_id,
    configure_logging,
    get_correlation_id,
    get_logger,
    reset_correlation_id,
)


@pytest.fixture(autouse=True)
def _setup_logging() -> None:
    configure_logging("DEBUG")


def _capture(buf: io.StringIO) -> structlog.stdlib.BoundLogger:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            lambda _l, _m, ed: {**ed, "correlation_id": get_correlation_id()},
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )
    return structlog.get_logger("test")


def test_correlation_id_propagates_in_log() -> None:
    buf = io.StringIO()
    log = _capture(buf)
    token = bind_correlation_id("c-123")
    try:
        log.info("hello")
    finally:
        reset_correlation_id(token)
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert payload["correlation_id"] == "c-123"
    assert payload["event"] == "hello"
    assert "ts" in payload


def test_correlation_id_isolated_across_async_tasks() -> None:
    buf = io.StringIO()
    log = _capture(buf)

    async def task(value: str) -> None:
        token = bind_correlation_id(value)
        try:
            await asyncio.sleep(0)
            log.info("inside", task=value)
        finally:
            reset_correlation_id(token)

    async def main() -> None:
        await asyncio.gather(task("alpha"), task("beta"))

    asyncio.run(main())
    lines = [json.loads(line) for line in buf.getvalue().strip().splitlines() if line]
    payloads = [line for line in lines if line.get("event") == "inside"]
    seen = {(line["task"], line["correlation_id"]) for line in payloads}
    assert ("alpha", "alpha") in seen
    assert ("beta", "beta") in seen


def test_get_logger_returns_bound_logger() -> None:
    log = get_logger("smoke")
    assert log is not None
