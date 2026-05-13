"""Global pytest fixtures for ai-steward-wiki tests.

Autouse fixture: clears ``structlog.contextvars`` between tests to prevent
cross-test correlation_id / identity-field leakage when middleware tests
bind values without explicit cleanup paths.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _clear_structlog_contextvars() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()
