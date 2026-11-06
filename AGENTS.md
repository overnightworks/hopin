# HopIn - Agent Guidance

This file holds HopIn's own facts on top of the global contract. `CLAUDE.md`
imports it.

## Commands

The `Makefile` owns the targets. Traps:

- `make e2e` and `make run` build the page first (`make frontend`, which needs Node 26, or 24, and pnpm), and the server refuses to
  start without that build. `make e2e` takes no lock itself: run it under the `flock` rule in the global contract's section "Machine load and local runs".
  It needs Playwright's Chromium installed once; the `End-to-end` job in `.github/workflows/ci.yml` owns that install step.
- `make wire-check` (part of `make lint`) fails when `frontend/src/lib/wire.ts` or
  `frontend/src/lib/features/game/wire.ts` drifts from the Python wire owners;
  regenerate them with `make wire`.
- CI's `Coverage` job judges 100 % over unit, integration and end-to-end data
  together, and the merged frontend report (Vitest and the browser) at 100 %
  lines, branches and functions; no local run judges it.

## Where things live

- A setting added to or changed in `src/hopin/config/settings.py` updates README's
  Configuration table in the same change.
- `quality/config.toml` configures the shared gate and has no `legacy` key, so legacy
  findings only report; `quality/baseline.toml` holds the size ratchet's baseline.
  The required check is `quality / verdict`; overnightworks/quality's README owns the
  gate's semantics.

## Scanner exceptions

- The scanner marker traps are in the `sonar-distributor` skill, section "Marker grammar".
