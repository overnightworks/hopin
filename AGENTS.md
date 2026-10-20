# HopIn - Agent Guidance

HopIn is a self-hosted video call service using WebRTC: a user starts a call and
shares a link, and friends join in the browser. The generic contract and code
policy (including the repository root layout) are the global contract's; this
file holds only HopIn's own facts. `CLAUDE.md` imports this file.

## Stack

Python 3.12+, `uv`, `ruff` (lint and format), `mypy --strict`, `pytest`,
`vulture` (dead code), `structlog` (logging, in `app.py` and `signaling/`).
Runtime settings are environment variables loaded through Pydantic Settings.

Pre-commit (`.pre-commit-config.yaml`) runs `ruff --fix`, `ruff-format`,
`mypy --strict` and a local pytest hook (`not integration and not e2e`).

## Commands

```bash
make install   # uv sync --all-extras
make format    # ruff check --fix + ruff format
make lint      # ruff check + ruff format --check + mypy
make test      # pytest, not integration and not e2e
make test-all  # pytest, not e2e (includes integration)
make e2e       # browser end-to-end tests (Playwright, no coverage)
make dead      # vulture
make layout    # root layout check
make check     # lint + layout + test + dead
make run       # start the server
```

## Structure

```
src/hopin/
├── auth/        # sign-in configuration built from the settings
├── config/      # settings.py, constants.py (application constants), enums
├── signaling/   # WebSocket signaling server
├── rooms/       # room and session management
├── web/         # HTTP routes, static files
└── errors.py    # custom exception types
scripts/         # repository tooling that CI and `make check` run
docs/mockups/    # the blessed pictures a surface change starts from
docs/decisions/  # decision records
quality/         # config.toml of the shared quality gate
```

## Tests

- `tests/unit/` mirrors `src/hopin/`; `tests/integration/` covers WebSocket and
  media IO; `tests/scripts/` mirrors `scripts/`.
- Markers (`--strict-markers`): `unit`, `integration`, `e2e`.
- Browser end-to-end tests live in `tests/e2e/` (Playwright, marker `e2e`), run
  with `make e2e` under `/tmp/probe-stack.lock`. They stay out of the coverage
  run; CI's `End-to-end` job is the gate.
- `pyproject.toml` runs pytest with `--cov --cov-fail-under=100`; the measured
  set is `[tool.coverage.run] source` (`src`, `scripts`).

## CI and SonarCloud

- CI's `ci` job ("Lint and test") runs lint, the root layout check and the tests;
  the `End-to-end` job runs the browser suite.
- The `quality` job (`overnightworks/quality`, pinned by SHA in `ci.yml`) runs the
  SonarCloud scan; its `quality / verdict` is the required check. It queries the
  findings directly rather than trusting the quality gate, which only grades
  ratings. A run on `master` fails on any open finding; other branch pushes only
  report them. A pull request fails only on new findings while `[gate] legacy` is
  absent from `quality/config.toml` (absent means "report"). The legacy rating is
  never chased; a fresh finding on any code is a merge blocker.
- The scan reads the coverage report the `ci` job uploads as the `coverage`
  artifact. `sonar-project.properties` carries only keys the gate allows, and
  `sonar.sources` stays narrow (never the root). Allowances for known secret
  false positives live only in `quality/config.toml`.
- Coverage exclusions: browser code under `src/hopin/web/static/`
  (`sonar.coverage.exclusions`, proven by the end-to-end suite, #45), and lines
  marked `pragma: no cover`, `if TYPE_CHECKING:` and `if __name__ == "__main__":`
  (`[tool.coverage.report]` in `pyproject.toml`).
- A finding whose code route was tried and refused is versioned as
  `# NOSONAR(<bare rule key>) reason`, for example `# NOSONAR(S7503) reason`:
  never the prefixed `python:S7503` form (SonarPython's marker parser rejects it)
  and never a bare `# NOSONAR`. Record the refusal on the distributor issue
  first, then add the marker.
- A ruff suppression is `# noqa: <code>`: bare codes, comma-separated, nothing
  else on the directive. The reason goes in a comment above the line. Prose
  appended after the code is read as a further code and SonarPython reports
  `python:S7632`.
- The SonarCloud UI is never used to accept or silence a finding; an "Accepted"
  or "False Positive" resolution set there still fails the gate.

## Local rules awaiting the operator's ruling

Each differs from the global contract. They stay here only until the operator rules.

- Test names follow `test_<method>_<scenario>_<expected_result>`. Differs from the contract: "its name states that behaviour", with no fixed pattern.
- Test-driven development: write the tests first, then implement. Differs from the contract: "a new behaviour is not done until a test pins it", with no order.
- Use dependency injection; never instantiate dependencies inside a class. Differs from the contract: no such rule.
- Async-first: `async/await` for all IO-bound operations. Differs from the contract: no such rule.
- Never catch bare `Exception` unless re-raising. Differs from the contract: stricter than "fail loud, no silent catches".
