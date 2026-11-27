# hopin

## Commands

`make` has no lister; the targets are in `Makefile`.

- `make test T=<modules>`: pytest on the named modules, outside the e2e and shared-stores markers.
- `make frontend-test F=<files>`: vitest on the named files. `make frontend-check`: type check and lint.
- Local runs name their tests, with pytest under `PYTEST_XDIST_AUTO_NUM_WORKERS=4`; CI runs the full suites, coverage and the full browser run.
- `make lint`: ruff, mypy, `wire-check` and `design-catalogs --check`.
- `make wire`: regenerates the generated `wire.ts` files from the Python wire owners.
- `make catalogs`: regenerates the two generated words modules (`frontend/src/lib/words/de.ts` and `src/hopin/words.py`, which the server and the tests read) from the catalog; `make lint` fails when they are stale.
- `make shared-stores T=<tests>`: `hopin migrate`, and two `hopin serve` processes against one Redis and one Postgres; `HOPIN_TEST_REDIS_URL` and `HOPIN_TEST_POSTGRES_URL` name them, and each test creates a database of its own on that Postgres. CI runs it in the `Shared stores` job.
- `make e2e T=<tests>`: the named browser tests. Builds the page first and needs Playwright's Chromium installed once (the `End-to-end` job in `.github/workflows/ci.yml` shows how). Run its drive under the global contract's `probe-run` rule.
- `make run`: builds the page, then `hopin migrate` and `hopin serve`. The server refuses to start without that build.

## Rules

- A `wire.ts` under `frontend/src/lib` is generated; change the Python wire owner and run `make wire`, never edit the file.
- A setting added to or changed in a settings class under `src/hopin` updates docs/configuration.md in the same change.
- The scanner marker traps are in the `sonar-distributor` skill, section "Marker grammar".
- A page text lives in the catalog: change `frontend/src/lib/catalogs/de.po` and run `make catalogs`, never edit a generated module.

## Owners

- The platform repository owns hopin's deployment.
- overnightworks/quality's README owns the quality gate's semantics.

## Documents

- `README.md`: read for what hopin offers, setup.
- `docs/configuration.md`: read for what each setting does, its default and its form.
- `docs/VISION.md`: read to know what hopin is and deliberately is not.
- `docs/ARCHITECTURE.md`: read before changing parts, a call's flow or the trust model.
- `docs/call.md`: read before changing a call's behaviour; it maps each rule to its test.
- `docs/game-master.md`: read before changing the game master; it maps each rule to its test.
- `docs/stage.md`: read before changing the stage on which a call shows a registered app's table.
- `docs/agent-endpoint.md`: read before changing the MCP endpoint, its admission or its tools.
- `docs/runbooks/release.md`: read before dispatching a release.
