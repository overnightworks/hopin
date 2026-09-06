.PHONY: install lint format test dead layout check run

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
	uv run pytest -m "not integration"

test-all:
	uv run pytest

dead:
	uv run vulture src/hopin

layout:
	uv run python scripts/check_root_layout.py

check: lint layout test dead

run:
	uv run hopin
