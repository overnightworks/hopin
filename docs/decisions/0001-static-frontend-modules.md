# 0001 Static frontend modules

Superseded by [ADR 0003](0003-page-layer-on-sveltekit.md).

## Context

The blessed UI (`docs/mockups/hopin-call.html`) is built in about fifteen
slices. If every slice edited one `call.html`, they would run one at a time.
The page is served under webauth's default Content-Security-Policy
(`script-src 'self'`, no inline style), which an inline `<script>` and `style=`
attributes would break.

## Decision

The page is markup, stylesheets and ES modules, served from
`src/hopin/web/static/`. Every route but the named public paths is behind
webauth's session gate, and the CSP and the Permissions-Policy come from
webauth:

- `index.html` (superseded: the layout in `frontend/src/routes/(call)/+layout.svelte` and the shell's `app.html` hold the markup now) holds markup only: no inline script, `<style>`, `style=` or
  `on*=` attribute. Every `src` and every `href` outside an `<a>` is a
  same-origin absolute path; an `<a href>` is one of the two central legal
  URLs or `/account`. The shell's legal links open in a new tab (`target="_blank" rel="noopener"`);
  the kit's sign-in card opens them in the same tab. CSP limits what the page
  fetches, not where a link goes. The built sign-in pages' guards read the rendered DOM in `tests/e2e/test_sign_in_pages.py`, the shell's in `tests/e2e/test_page_shell.py`.
- The shell loads the built kit stylesheet, then `hopin.css`, then the built stylesheets of the kit components it renders; the prerendered sign-in pages load the built kit stylesheet and the card's, then `hopin.css`. The kit is the
  installed `overnightworks-appkit` npm package: the root layout imports its stylesheet once and Vite builds it and its fonts into
  hashed `/_app/` files. The gate admits the exact stylesheet and font files it must open, derived from the built
  sign-in pages' linked stylesheets, never a directory.
- `assets/` is mounted at `/static` (`STATIC_PATH`); only `css/` and `img/` (the
  brand mark every page links as its icon) are reachable, so the page
  is served only at its routes, never from the mount.
- The home, pre-join and end footers render the kit's `LegalLinks` at the page place with `newTab` (the kit's default would open the links in the same tab, which would end a call). The guard test pins that each distinct `data-screen` value holds
  exactly one legal nav with both URLs (the e2e module `tests/e2e/test_page_shell.py` reads it from the DOM); the call screen is one element whose nav sits in
  its More menu, which is still hopin's own markup until #427 decides.
- Signed out, Login failed and No access are appkit's `SignInPage`, prerendered from
  `frontend/src/routes/(sign-in)/`. The `<a>`
  rule above is about `index.html` and `hopin.css`; a sign-in page links the
  two legal URLs and `/auth/start`, except No access, which links only the two legal URLs. No access and the account menu post one
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

- There is no build step for the modules; the browser loads them as written (the shell is built since
  ADR 0003). The one installed package the pages' styles come from is the `overnightworks-appkit`
  npm package.
- Browser code is excluded from Python coverage and Sonar coverage
  (`sonar.coverage.exclusions`); its proof is the end-to-end suite (#45).
- Token ownership: appkit owns the neutrals, the states and `color-scheme`, and the shell's fonts come from the kit (`--font-body`); `hopin.css` owns the slot, stage, shadow, motion and mono tokens and defines none of the kit's. `.sign-in` in `hopin.css` wraps the built sign-in pages as their scroll container.
