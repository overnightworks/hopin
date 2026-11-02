# 0003 Page layer on SvelteKit

## Context

The page layer moves from hand-written static files to SvelteKit, one slice at a
time, with no visible change. The first slice that serves the build replaces
`index.html` with the built shell. The page runs under webauth's
Content-Security-Policy (`script-src 'self'`, `style-src 'self'`), which the
shell's own inline start script and SvelteKit's route announcer would break.

## Decision

- `make frontend` builds `frontend/` with adapter-static into `src/hopin/web/build/`.
  The build is never committed; the image and CI build it, and the server
  refuses to start without it (`PageBuild.load`, naming `make frontend`).
- The layout (`frontend/src/routes/(call)/+layout.svelte`) holds the markup that
  `index.html` held and calls the start-up module's `startUp()` from `onMount`
  (`frontend/src/routes/(call)/start-up.ts`, the one wiring, which runs nothing at
  import), so the wiring runs only after the markup exists. Each feature is
  bundled under `frontend/src/lib/features/<name>/` and activated by
  `GET /api/call-features`; the call's code never imports it.
- `/` and `/c/{room}` answer the built shell as read at start. Its inline
  scripts are admitted by hash, computed from those same bytes, never with
  `'unsafe-inline'`; the shell carries a strong `ETag` and `no-cache`.
- `style-src` stays `'self'` plus `'unsafe-hashes'` and the hash of the one style
  SvelteKit's announcer writes (the text of kit 3.1.0 `root.svelte`); a policy
  violation anywhere is a defect. `hopin.css` hides `#svelte-announcer` itself,
  because Firefox strips the admitted style. Bumping SvelteKit re-derives the hash.
- The `(sign-in)` route group (Signed out, Login failed and No access, rendered from appkit's
  `SignInPage`) is prerendered with `csr = false` and absolute asset paths
  (`paths.relative = false`), so a page carries no script and its links resolve at
  any served address. `PageBuild` serves them at `/login`, at `/no-access` and as the refused
  callback's 403.
- `/_app` is one mount of the build, behind the session gate, except the exact
  stylesheet and font files the built sign-in pages' stylesheets reference, read at start. Its `immutable/`
  files answer `private, max-age=31536000, immutable` on success from the mount
  itself, never from a path rule: the outermost middleware applies a rule to the
  401, 429 and 404 of the same path too, and `public` would let a shared cache
  hand a gated file out. Every other file there is `no-cache`.

## Consequences

- The unit job needs no Node: tests serve a committed fixture build.
  The end-to-end suite and the removal drill take the real build from the
  `Frontend` job.
- A new SvelteKit release can change the announcer's style; the browser test of
  the policy catches it.
- The page's static guards for the shell read the rendered DOM in an e2e test.

## Rules carried over from ADR 0001

- The shell's legal links open in a new tab (`target="_blank" rel="noopener"`); the
  kit's sign-in card opens them in the same tab. CSP limits what the page fetches,
  not where a link goes.
- `/static` (`STATIC_PATH`) serves only `css/` and `img/`.
- Showing and hiding is the `hidden` class; a native `<dialog>` uses its own
  `show()` and `close()`, never `showModal()`.
- Script sets styles only through the CSSOM, which the policy allows; handlers come
  from `addEventListener`.
- The sign-in pages link the two legal URLs and `/auth/start`, except No access,
  which links only the two legal URLs; No access and the account menu post one
  same-origin form to `/logout`.
- Token ownership: appkit owns the neutrals, the states and `color-scheme`, and the
  shell's fonts come from the kit (`--font-body`); `hopin.css` owns the slot, stage,
  shadow, motion and mono tokens and defines none of the kit's. `.sign-in` in
  `hopin.css` wraps the built sign-in pages as their scroll container.
- Stylesheet order: the kit's built stylesheet, then `hopin.css`, then the built
  stylesheets of the components the shell renders. The kit is the installed
  `overnightworks-appkit` package, imported once by the root layout. Guarded by
  `tests/e2e/test_page_shell.py`.
- The markup holds no inline script, `<style>`, `style=` or `on*=` attribute. Every
  `src` and every `href` outside an `<a>` is a same-origin absolute path; an
  `<a href>` is one of the two central legal URLs or `/account`. Guarded by
  `tests/e2e/test_page_shell.py`.
