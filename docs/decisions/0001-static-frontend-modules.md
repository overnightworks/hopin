# 0001 Static frontend modules

## Context

The blessed UI (`docs/mockups/hopin-call.html`) is built in about fifteen
slices. If every slice edited one `call.html`, they would run one at a time.
The login slice also needs webauth's default Content-Security-Policy
(`script-src 'self'`, no inline style), which an inline `<script>` and `style=`
attributes would break.

## Decision

The page is markup, one stylesheet and ES modules, served from
`src/hopin/web/static/`:

- `index.html` holds markup only: no inline script, `<style>`, `style=` or
  `on*=` attribute, and every `src`/`href` is a same-origin absolute path. A
  guard test (`tests/unit/test_web.py`) keeps it so.
- `assets/` is mounted at `/static` (`STATIC_PATH`); only `css/` and `js/` are
  reachable, so the page has a single address.
- Showing and hiding is the `hidden` class. Styles set from script go through
  the CSSOM, which the policy allows. Handlers come from `addEventListener`.
- `app.js` is the only entry point and the only wiring. No module imports it,
  and no module runs another module's code at load. Leaving the call and the
  peer teardown on reconnect live there, so `signaling.js` never imports
  `mesh.js`.
- `signaling.js` offers `on(type, handler)`; each module registers the message
  types it handles, so a slice adds a handler instead of editing a `switch`.
- `state.js` is the one mutable call state and has no DOM.

| Module | Owns |
|---|---|
| `app.js` | wiring, call start and leave, session message handlers |
| `state.js` | call and local-media state |
| `signaling.js` | WebSocket, `SIGNAL_TYPE`, reconnect, `send`, `on` |
| `mesh.js` | peer connections, the ICE servers from `/api/ice-servers`, track sync to peers |
| `media.js` | local media, camera and screen share |
| `quality.js` | round-trip indicator |
| `chat.js` | chat panel and messages |
| `sounds.js` | notification sounds |
| `stage.js` | video tiles, grid, local preview |
| `controls.js` | control bar and share box |
| `status.js` | header status, lock indicator, participant count |
| `dialogs.js` | setup and leave dialogs |

## Consequences

- There is no build step and no runtime dependency; the browser loads the
  modules as written.
- Browser code is excluded from Python coverage and Sonar coverage
  (`sonar.coverage.exclusions`); its proof is the end-to-end suite (#45).
- Row 14 of #25 vendors `ui-console.css` v0.2.0 beside `hopin.css`.
