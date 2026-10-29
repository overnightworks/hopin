.PHONY: install lint wire-check format test dead check run e2e frontend frontend-check

install:
	uv sync --all-extras

lint:
	uv run ruff check .
	uv run ruff format --check src tests
	uv run mypy src
	$(MAKE) wire-check

wire-check:
	PYTHONPATH=src uv run python scripts/generate_wire.py --check

format:
	uv run ruff check --fix .
	uv run ruff format src tests

test:
	uv run pytest -m "not e2e"

e2e:
	uv run pytest -m e2e --no-cov --output test-results

dead:
	uv run vulture src/hopin

frontend:
	cd frontend && pnpm install --frozen-lockfile && pnpm build

frontend-check:
	cd frontend && pnpm check && pnpm lint

check: lint test dead frontend-check

run:
	uv run hopin serve
