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
  `index.html` held and imports `#lib/legacy/app.js` dynamically from `onMount`, so the
  legacy code, bundled from `frontend/src/lib/legacy/`, runs only after the
  markup exists; a lint rule refuses any static import of it. Each feature is
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
