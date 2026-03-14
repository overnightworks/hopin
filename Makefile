.PHONY: install lint format test dead check run

install:
	uv sync --all-extras

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src

format:
	uv run ruff check --fix src tests
	uv run ruff format src tests

test:
	uv run pytest -m "not integration"

test-all:
	uv run pytest

dead:
	uv run vulture src/hopin

check: lint test dead

run:
	uv run hopin
