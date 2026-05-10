.PHONY: help install lint format test qa clean

help:
	@echo "make install   - uv sync (incl. dev group)"
	@echo "make lint      - ruff check + ruff format --check + mypy --strict src/"
	@echo "make format    - ruff format + ruff check --fix"
	@echo "make test      - pytest tests/"
	@echo "make qa        - lint + test"

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest tests/

qa: lint test

clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache build dist *.egg-info htmlcov .coverage
