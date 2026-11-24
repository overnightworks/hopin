.PHONY: verdict-repeatability install lint wire wire-check catalogs format test shared-stores dead run e2e frontend frontend-check frontend-test

# Fails the target loud unless the variable $(1) names what to run; $(2) is an example.
require = $(if $($(1)),,$(error name what to run with $(1)=..., for example make $(2)))

install:
	uv sync --all-extras

lint:
	uv run ruff check .
	uv run ruff format --check src tests
	uv run mypy src scripts
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
	uv run ruff format src tests

test:
	$(call require,T,test T=tests/test_app.py)
	uv run pytest -m "not e2e and not shared_stores" $(T)

e2e: frontend
	$(call require,T,e2e T=tests/e2e/<module>)
	uv run pytest -m e2e --no-cov --output test-results $(T)

# HOPIN_TEST_REDIS_URL and HOPIN_TEST_POSTGRES_URL name the stores; the Postgres user may create databases.
shared-stores:
	$(call require,T,shared-stores T=tests/<module>)
	uv run pytest -m shared_stores --no-cov $(T)

dead:
	uv run vulture src/hopin

frontend:
	cd frontend && pnpm install --frozen-lockfile && pnpm build

frontend-check:
	cd frontend && pnpm check && pnpm lint

frontend-test:
	$(call require,F,frontend-test F=src/lib/<file>.test.ts)
	cd frontend && pnpm exec vitest run $(F)

run: frontend
	uv run hopin migrate
	uv run hopin serve

# Costs real model calls on the configured gateway; never part of CI.
verdict-repeatability:
	uv run hopin verdict-repeatability --runs $(or $(RUNS),10)
