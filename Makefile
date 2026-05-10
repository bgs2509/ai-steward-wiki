.PHONY: help install lint ruff-check ruff-format-check mypy grace-lint format test test-unit test-integration qa total-test clean

# Fail-fast: cheapest checks first, then heavier ones.
# Order rationale (Google/Stripe SRE Make conventions):
#   1. ruff check         — ~1s, catches syntax/style/imports
#   2. ruff format --check — ~1s, formatting drift
#   3. mypy --strict      — seconds, type errors
#   4. grace lint         — seconds, semantic-markup + governance
#   5. pytest unit        — fast, isolated
#   6. pytest integration — slow, real Claude CLI / DB I/O
# Each target shells out fresh; Make halts on the first non-zero exit.

help:
	@echo "make install          - uv sync (incl. dev group)"
	@echo "make lint             - ruff check + ruff format --check + mypy --strict src/"
	@echo "make grace-lint       - grace lint --failOn errors"
	@echo "make format           - ruff format + ruff check --fix"
	@echo "make test             - pytest tests/"
	@echo "make test-unit        - pytest tests/unit"
	@echo "make test-integration - RUN_INTEGRATION=1 pytest tests/integration"
	@echo "make qa               - lint + grace-lint + test"
	@echo "make total-test       - full fail-fast pipeline: ruff + mypy + grace + unit + integration"
	@echo "make clean            - remove caches and build artifacts"

install:
	uv sync

ruff-check:
	uv run ruff check .

ruff-format-check:
	uv run ruff format --check .

mypy:
	uv run mypy src

lint: ruff-check ruff-format-check mypy

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest tests/

test-unit:
	uv run pytest tests/unit -q

test-integration:
	@if [ -d tests/integration ]; then \
		RUN_INTEGRATION=1 uv run pytest tests/integration -q; \
	else \
		echo "tests/integration not present — skipping"; \
	fi

grace-lint:
	grace lint --failOn errors

qa: lint grace-lint test

# Full quality gate. Order is intentional: cheapest checks first so the
# pipeline fails as early as possible. Mirrors what CI runs on every PR.
total-test: ruff-check ruff-format-check mypy grace-lint test-unit test-integration
	@echo ""
	@echo "✓ total-test passed: ruff + mypy + grace lint + unit + integration"

clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache build dist *.egg-info htmlcov .coverage
