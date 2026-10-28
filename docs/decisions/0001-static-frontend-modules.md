# 0001 Static frontend modules

## Context

The blessed UI (`docs/mockups/hopin-call.html`) is built in about fifteen
slices. If every slice edited one `call.html`, they would run one at a time.
The page is served under webauth's default Content-Security-Policy
(`script-src 'self'`, no inline style), which an inline `<script>` and `style=`
attributes would break.

## Decision

The page is markup, two stylesheets and ES modules, served from
`src/hopin/web/static/`. Every route but the named public paths is behind
webauth's session gate, and the CSP and the Permissions-Policy come from
webauth:

- `index.html` holds markup only: no inline script, `<style>`, `style=` or
  `on*=` attribute. Every `src` and every `href` outside an `<a>` is a
  same-origin absolute path; an `<a href>` is only one of the two central legal
  URLs, with `target="_blank" rel="noopener"`. CSP limits what the page
  fetches, not where a link goes. A guard test (`tests/web/test_routes.py`) keeps
  it so.
- The stylesheets are `ui-console.css`, vendored verbatim and loaded first,
  then `hopin.css`. An upgrade of the first is a new vendoring, never an edit.
- `assets/` is mounted at `/static` (`STATIC_PATH`); only `css/`, `img/` (the
  brand mark every page links as its icon) and `js/` are reachable, so the page
  is served only at its routes, never from the mount.
- Every screen carries its own `<nav class="legal">` copy (a static page has no
  templating). The guard test pins that each distinct `data-screen` value holds
  exactly one, with both URLs; the call screen is one element whose nav sits in
  its More menu.
- HopIn reuses no class name that `ui-console.css` defines, because its grammar
  classes would otherwise style HopIn's markup. A name that both the
  picture and ui-console define is renamed in HopIn. The test
  `test_reuses_no_class_name_that_ui_console_defines` enforces the rule.
- The sign-in pages (`login-failed.html`, `login.html` for Signed out and
  `no-access.html`) are ui-console's sign-in markup with its classes and
  wording, and load both stylesheets. The no-reuse rule and the legal-only `<a>`
  rule above are about `index.html` and `hopin.css`; a sign-in page links the
  two legal URLs and `/auth/start`. No access and the account menu post one
  same-origin form to `/logout`, which answers webauth's own end-session page
  under its own policy, so HopIn's policy adds no issuer source.
- Showing and hiding is the `hidden` class; a native `<dialog>` uses its own `show()` and `close()` (never `showModal()`). Styles set from script go through
  the CSSOM, which the policy allows. Handlers come from `addEventListener`.
- `app.js` is the only entry point and the only wiring. No module imports it,
  and no module runs another module's code at load. Leaving the call and the
  peer teardown when a session is lost (a refused resume or a reconnect that
  gave up) or ended (the server closes with `SESSION_ENDED_CLOSE_CODE`, and the page signs in again)
  live there, so `signaling.js` never imports `mesh.js`.
- `signaling.js` offers `on(type, handler)`; each module registers the message
  types it handles, so a slice adds a handler instead of editing a `switch`.
- `state.js` is the one mutable call state and has no DOM.

## Consequences

- There is no build step and no runtime dependency; the browser loads the
  modules as written.
- Browser code is excluded from Python coverage and Sonar coverage
  (`sonar.coverage.exclusions`); its proof is the end-to-end suite (#45).
- `ui-console.css` is byte-identical to its release and loaded first; the test `test_vendored_ui_console_is_byte_identical_to_its_release` pins its SHA-256.
