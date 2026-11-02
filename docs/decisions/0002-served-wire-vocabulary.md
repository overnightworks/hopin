# 0002 Served wire vocabulary

Status: superseded by [ADR 0003](0003-page-layer-on-sveltekit.md). The bundle
reads generated TypeScript wires, and `/static/js/wire.js` is gone.

## Context

The signaling protocol's tokens (signal types, error and room-closed reasons,
the replay log limit, the replay sets, the WebSocket path and the session-ended
close code) had two hand-edited owners: Python constants and a copy in the
browser modules. A drift showed only at runtime.

## Decision

The server serves the vocabulary to the browser as one ES module,
`/static/js/wire.js`, rendered by `src/hopin/web/wire.py` from the Python
owners with `json.dumps` for every value. The call core in the bundle
(`signaling.js`, `replay.js`) imports the same tokens from the generated
`frontend/src/lib/wire.ts`; the game's module and `FEATURE_MODULES` in `app.js`
read the served module. This is the one exception to ADR 0001's
"served from `src/hopin/web/static/`": the module has no file there, and the
route sits before the `/static` mount, so it inherits the `/static/` no-cache
rule and the session gate like every other script.

## Consequences

- A token is changed once, in Python; the browser follows on the next load.
- There is still no build step and no runtime dependency.
- The game's module imports `wire.js` by path, so a page that loads it needs the
  server to serve it; the end-to-end suite proves the page loads.
