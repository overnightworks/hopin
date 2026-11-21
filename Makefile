.PHONY: verdict-repeatability install lint wire wire-check catalogs format test shared-stores dead check run e2e frontend frontend-check

install:
	uv sync --all-extras

lint:
	uv run ruff check .
	uv run ruff format --check src tests packages
	uv run mypy src scripts packages/probe/src
	$(MAKE) wire-check
	uv run design-catalogs --check

wire:
	uv run python scripts/generate_wire.py

wire-check:
	uv run python scripts/generate_wire.py --check

catalogs:
	uv run design-catalogs

format:
	uv run ruff check --fix .
	uv run ruff format src tests packages

test:
	uv run pytest -m "not e2e and not shared_stores"

e2e: frontend
	uv run pytest -m e2e --no-cov --output test-results

shared-stores:
	uv run pytest -m shared_stores --no-cov

dead:
	uv run vulture src/hopin packages/probe/src/hopin_probe

frontend:
	cd frontend && pnpm install --frozen-lockfile && pnpm build

frontend-check:
	cd frontend && pnpm check && pnpm lint && pnpm test

check: lint test dead frontend-check

run: frontend
	uv run hopin serve

# Costs real model calls on the configured gateway; never part of `make check` or CI.
verdict-repeatability:
	uv run hopin verdict-repeatability --runs $(or $(RUNS),10)
