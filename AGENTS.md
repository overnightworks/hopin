# hopin

## Commands

`make` has no lister; the targets are in `Makefile`.

- `make check`: every local check (lint, unit and integration tests, dead code, frontend checks).
- `make lint`: ruff, mypy, `wire-check` and `design-catalogs --check`.
- `make wire`: regenerates the generated `wire.ts` files from the Python wire owners.
- `make catalogs`: regenerates the two generated words modules (`frontend/src/lib/words/de.ts` and `src/hopin/words.py`, which the server and the tests read) from the catalog; `make lint` fails when they are stale.
- `make e2e`: the browser tests. Builds the page first and needs Playwright's Chromium installed once (the `End-to-end` job in `.github/workflows/ci.yml` shows how). Run its drive under the global contract's `probe-run` rule.
- `make run`: builds the page, then `hopin serve`. The server refuses to start without that build.

## Rules

- A `wire.ts` under `frontend/src/lib` is generated; change the Python wire owner and run `make wire`, never edit the file.
- A setting added to or changed in `src/hopin/config/settings.py` updates README's Configuration table in the same change.
- The scanner marker traps are in the `sonar-distributor` skill, section "Marker grammar".
- A page text lives in the catalog: change `frontend/src/lib/catalogs/de.po` and run `make catalogs`, never edit a generated module.

## Owners

- The platform repository owns hopin's deployment.
- overnightworks/quality's README owns the quality gate's semantics.

## Documents

- `README.md`: read for what hopin offers, setup, configuration and the release path.
- `docs/VISION.md`: read to know what hopin is and deliberately is not.
- `docs/ARCHITECTURE.md`: read before changing parts, a call's flow or the trust model.
- `docs/call.md`: read before changing a call's behaviour; it maps each rule to its test.
- `docs/game-master.md`: read before changing the game master; it maps each rule to its test.
- `docs/skat.md`: read before changing the Skat table on a call's stage.
