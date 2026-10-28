.PHONY: install lint format test dead check run e2e

install:
	uv sync --all-extras

lint:
	uv run ruff check .
	uv run ruff format --check src tests
	uv run mypy src

format:
	uv run ruff check --fix .
	uv run ruff format src tests

test:
	uv run pytest -m "not e2e"

e2e:
	uv run pytest -m e2e --no-cov --output test-results

dead:
	uv run vulture src/hopin

check: lint test dead

run:
	uv run hopin serve
