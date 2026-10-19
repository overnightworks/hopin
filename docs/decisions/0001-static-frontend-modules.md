# 0001 Static frontend modules

## Context

The blessed UI (`docs/mockups/hopin-call.html`) is built in about fifteen
slices. If every slice edited one `call.html`, they would run one at a time.
The login slice also needs webauth's default Content-Security-Policy
(`script-src 'self'`, no inline style), which an inline `<script>` and `style=`
attributes would break.

## Decision

The page is markup, two stylesheets and ES modules, served from
`src/hopin/web/static/`:

- `index.html` holds markup only: no inline script, `<style>`, `style=` or
  `on*=` attribute. Every `src` and every `href` outside an `<a>` is a
  same-origin absolute path; an `<a href>` is only one of the two central legal
  URLs, with `target="_blank" rel="noopener"`. CSP limits what the page
  fetches, not where a link goes. A guard test (`tests/unit/test_web.py`) keeps
  it so.
- The stylesheets are `ui-console.css`, vendored verbatim and loaded first,
  then `hopin.css`. An upgrade of the first is a new vendoring, never an edit.
- Every screen carries its own `<nav class="legal">` copy (a static page has no
  templating). The guard test pins that each distinct `data-screen` value holds
  exactly one, with both URLs; the call screen spans two elements and its nav
  sits in the header.
- HopIn reuses no class name that `ui-console.css` defines, because its grammar
  classes would otherwise style HopIn's markup. Six names are defined in both
  the picture and ui-console: `.btn` (now `.button`) and `.glyph` (now
  `.brand-mark`) are renamed; `.field` and `.sub` (row 16), `.row` (rows 19 and
  21) and `.banner` (row 21) are renamed by the row that builds them. A unit
  test enforces the rule.
- Showing and hiding is the `hidden` class. Styles set from script go through
  the CSSOM, which the policy allows. Handlers come from `addEventListener`.
- `app.js` is the only entry point and the only wiring. No module imports it,
  and no module runs another module's code at load. Leaving the call and the
  peer teardown when a session is lost (a refused resume or a reconnect that
  gave up) live there, so `signaling.js` never imports `mesh.js`.
- `signaling.js` offers `on(type, handler)`; each module registers the message
  types it handles, so a slice adds a handler instead of editing a `switch`.
- `state.js` is the one mutable call state and has no DOM.

| Module | Owns |
|---|---|
| `app.js` | wiring, call start and leave, session message handlers |
| `router.js` | the address and the screen: `/c/{id}` parsing, call links, the screen switch |
| `home.js` | the home screen |
| `state.js` | call and local-media state |
| `signaling.js` | WebSocket, `SIGNAL_TYPE`, reconnect and resume, `send`, `on` |
| `replay.js` | the numbered log of sent frames that a resume resends |
| `mesh.js` | peer connections, the ICE servers from `/api/ice-servers`, track sync to peers |
| `recovery.js` | when and how often a failed peer connection restarts ICE |
| `bandwidth.js` | the video upload budget and the cap on each video sender |
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
- `ui-console.css` is v0.2.0, byte-identical to its release and loaded first.
