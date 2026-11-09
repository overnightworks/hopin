# hopin

## Commands

`make` has no lister; the targets are in `Makefile`.

- `make check`: every local check (lint, unit and integration tests, dead code, frontend checks).
- `make lint`: ruff, mypy and `wire-check`.
- `make wire`: regenerates the generated `wire.ts` files from the Python wire owners.
- `make e2e`: the browser tests. Builds the page first and needs Playwright's Chromium installed once (the `End-to-end` job in `.github/workflows/ci.yml` shows how). Run it under the global contract's `flock` rule.
- `make run`: builds the page, then `hopin serve`. The server refuses to start without that build.

## Rules

- A `wire.ts` under `frontend/src/lib` is generated; change the Python wire owner and run `make wire`, never edit the file.
- A setting added to or changed in `src/hopin/config/settings.py` updates README's Configuration table in the same change.
- `quality/config.toml` has no `legacy` key, so legacy findings only report; `quality/baseline.toml` is the size ratchet's baseline.
- The scanner marker traps are in the `sonar-distributor` skill, section "Marker grammar".

## Owners

- The `Release` workflow runs only on the operator's own word, given for that release.
- The platform deploys hopin; the image ref reaches production in a platform pull request the operator merges.
- overnightworks/quality's README owns the gate's semantics; the required check is `quality / verdict`.

## Documents

- `README.md`: read for what hopin offers, setup, configuration and the release path.
- `docs/VISION.md`: read to know what hopin is and deliberately is not.
- `docs/ARCHITECTURE.md`: read before changing parts, a call's flow or the trust model.
- `docs/call.md`: read before changing a call's behaviour; it maps each rule to its test.
- `docs/game-master.md`: read before changing the game master; it maps each rule to its test.
