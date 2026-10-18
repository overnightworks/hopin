.PHONY: install lint format test dead layout check run e2e

install:
	uv sync --all-extras

lint:
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts
	uv run mypy src scripts

format:
	uv run ruff check --fix src tests scripts
	uv run ruff format src tests scripts

test:
	uv run pytest -m "not integration and not e2e"

test-all:
	uv run pytest -m "not e2e"

e2e:
	uv run pytest -m e2e --no-cov --output test-results

dead:
	uv run vulture src/hopin

layout:
	uv run python scripts/check_root_layout.py

check: lint layout test dead

run:
	uv run hopin
