# HopIn - Agent Guidance

This file holds HopIn's own facts on top of the global contract. `CLAUDE.md`
imports it.

## Commands

The `Makefile` owns the targets. Traps:

- `make e2e` takes no lock itself: hold `/tmp/probe-stack.lock` around it
  (`flock /tmp/probe-stack.lock make e2e`). It needs Playwright's Chromium installed
  once; the `End-to-end` job in `.github/workflows/ci.yml` owns that install step.
- CI's `Coverage` job judges 100 % over unit, integration and end-to-end data
  together; a local run reports coverage but does not judge it.

## Where things live

- A setting added to or changed in `src/hopin/config/settings.py` updates README's
  Configuration table in the same change.
- `quality/config.toml` configures the shared gate and has no `legacy` key, so legacy
  findings only report; `quality/baseline.toml` holds the size ratchet's baseline.
  The required check is `quality / verdict`; overnightworks/quality's README owns the
  gate's semantics.

## Scanner exceptions

- The scanner marker traps are in the `sonar-distributor` skill, section "Marker grammar".

## Local rules awaiting the operator's ruling

Each differs from the global contract. They stay here only until the operator rules.

- Test names follow `test_<method>_<scenario>_<expected_result>`. Differs from the contract: "its name states that behaviour", with no fixed pattern.
- Test-driven development: write the tests first, then implement. Differs from the contract: "a new behaviour is not done until a test pins it", with no order.
- Use dependency injection; never instantiate dependencies inside a class. Differs from the contract: no such rule.
- Async-first: `async/await` for all IO-bound operations. Differs from the contract: no such rule.
- Never catch bare `Exception` unless re-raising. Differs from the contract: stricter than "fail loud, no silent catches".
