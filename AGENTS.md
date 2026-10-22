# HopIn - Agent Guidance

HopIn is a self-hosted video call service using WebRTC: a user starts a call and
shares a link, and friends join in the browser. The generic contract and code
policy (including the repository root layout) are the global contract's; this
file holds only HopIn's own facts. `CLAUDE.md` imports this file.

## Commands

The `Makefile` owns the targets. Traps:

- `make check` runs neither the integration nor the browser tests; `make test-all`
  adds integration.
- `make e2e` starts its own server and Chromium, runs under `/tmp/probe-stack.lock`
  and stays out of coverage; CI's `End-to-end` job is its gate.
- `--cov-fail-under=100` sits in pytest's `addopts` (`pyproject.toml`), so a
  targeted `pytest` run fails on coverage unless it passes `--no-cov`.

## Where things live

- Every top-level module of `hopin` stands in one layer of the `layers` contract in
  `pyproject.toml` (`[tool.importlinter]`), which quality's `import-contract` rule
  enforces in CI: a new top-level module needs its layer there.
- A setting added to or changed in `config/settings.py` updates README's
  Configuration table in the same change.
- `docs/mockups/hopin-call.html` is the blessed picture a surface change starts
  from; `docs/decisions/` holds the decision records.
- `quality/config.toml` configures the shared gate and has no `legacy` key, so legacy
  findings only report; `quality/baseline.toml` holds the size ratchet's baseline.
  The required check is `quality / verdict`; overnightworks/quality's README owns the
  gate's semantics.

## Local rules awaiting the operator's ruling

Each differs from the global contract. They stay here only until the operator rules.

- Test names follow `test_<method>_<scenario>_<expected_result>`. Differs from the contract: "its name states that behaviour", with no fixed pattern.
- Test-driven development: write the tests first, then implement. Differs from the contract: "a new behaviour is not done until a test pins it", with no order.
- Use dependency injection; never instantiate dependencies inside a class. Differs from the contract: no such rule.
- Async-first: `async/await` for all IO-bound operations. Differs from the contract: no such rule.
- Never catch bare `Exception` unless re-raising. Differs from the contract: stricter than "fail loud, no silent catches".
