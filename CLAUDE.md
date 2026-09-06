# HopIn - Development Guidelines

## Project Overview

HopIn is a self-hosted video call service using WebRTC.
Users start a call and share a link — friends click and join instantly in the browser.

## Tech Stack

- **Language**: Python 3.12+
- **Package Manager**: uv
- **Linting/Formatting**: ruff
- **Type Checking**: mypy (strict mode)
- **Testing**: pytest
- **Dead Code Detection**: vulture
- **Pre-commit**: pre-commit hooks for all checks

## Code Style

### General Principles

- Write self-explaining code. Method and variable names must convey intent.
- No inline comments unless explaining *why* something non-obvious is done (never explain *what*).
- No hardcoded strings or magic numbers. Use constants and configuration values.
- No god classes. Each class has a single responsibility.
- Keep methods short and focused. If a method needs a comment to explain what it does, rename it or split it.
- Use dependency injection. Never instantiate dependencies inside a class — pass them in.
- Use structured logging via `structlog`. Never use `print()` for operational output.

### Python Specific

- All code must pass `mypy --strict` with no ignores unless explicitly justified.
- All code must pass `ruff check` and `ruff format` with zero violations.
- Use `typing` annotations on all function signatures and class attributes.
- Prefer dataclasses or Pydantic models over plain dicts for structured data.
- Use `pathlib.Path` over `os.path`.
- Use `enum.Enum` for fixed sets of values.
- Async-first: use `async/await` for all IO-bound operations.

### Constants and Configuration

- Application constants go in `src/hopin/config/constants.py`.
- Runtime configuration uses environment variables loaded via Pydantic Settings.
- Never scatter string literals or magic numbers across the codebase.

### Error Handling

- Define custom exception types in `src/hopin/errors.py`.
- Never catch bare `Exception` unless re-raising.
- Let unexpected errors propagate — do not swallow them silently.

## Testing

### Approach

- Test-driven development: write tests first, then implement.
- 100% coverage on business logic (signaling, rooms, config).
- Integration tests for IO-heavy code (WebSocket, media).
- Overall coverage target: 100 %, enforced by `--cov-fail-under=100`.

### Structure

- Unit tests in `tests/unit/`, mirroring `src/hopin/` structure.
- Integration tests in `tests/integration/`.
- Use `pytest` fixtures for setup, no test inheritance hierarchies.
- Test names follow `test_<method>_<scenario>_<expected_result>` pattern.
- No mocking of the system under test. Mock only external boundaries.

## SonarCloud

- The bar is zero open findings and 100 % coverage where it makes sense: the
  only exclusions are lines marked `pragma: no cover`, `if TYPE_CHECKING:`,
  and the `if __name__ == "__main__":` entry point, all named in
  `[tool.coverage.report]` in `pyproject.toml`. The legacy rating is never
  chased; a fresh finding on any code is a merge blocker.
- CI's "SonarCloud open findings" step fails on any unresolved finding in
  scope: PR scope on pull requests, the whole master branch on push. It
  queries the public API's `issueStatuses` directly rather than trusting the
  quality gate, which only grades ratings.
- A finding whose code route was tried and refused is versioned in code as
  `# NOSONAR(<bare rule key>) reason` — e.g. `# NOSONAR(S7503) reason`, never
  the prefixed `python:S7503` form (SonarPython's marker parser rejects it)
  and never a bare `# NOSONAR` with no rule key. Record the refusal on the
  distributor issue first, then add the marker.
- The SonarCloud UI is never used to accept or silence a finding — an
  "Accepted" or "False Positive" resolution set there still fails the gate.
  The only accepted silencing mechanism is the versioned NOSONAR marker above.

## Repository Layout

The rule for what lives at the repository root is owned by `AGENTS.md`,
section "Repository layout".

## Project Structure

```
src/hopin/
├── config/         # settings, constants, enums
├── signaling/      # WebSocket signaling server
├── rooms/          # room/session management
├── web/            # HTTP routes, static files
├── media/          # WebRTC media handling
└── errors.py       # custom exceptions
```

## Development Commands

```bash
make install    # install dependencies
make lint       # run ruff check + ruff format --check + mypy
make test       # run pytest with coverage
make dead       # run vulture for dead code detection
make check      # run all checks (lint + test + dead)
make run        # start the server
```

## Git Practices

- Commit messages: imperative mood, concise, explain *why* not *what*.
- Small, focused commits. One concern per commit.
- Never commit secrets, credentials, or `.env` files.
