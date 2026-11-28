# HopIn

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![CI](https://github.com/overnightworks/hopin/actions/workflows/ci.yml/badge.svg)](https://github.com/overnightworks/hopin/actions/workflows/ci.yml)

HopIn is a self-hosted video call service. A signed-in member starts a call and shares its link, and friends join in the browser with nothing to install. The server signs people in and relays the setup messages between the members of a call; their chat lines go straight between the browsers. The camera video and the call's sound between the members flow directly between the browsers, or through the TURN relay when it is switched on and a direct path fails, still encrypted between the browsers. Three things do pass through the server: the game master's and agents' spoken sentences, which it has the speech service render and serves to each browser; while an agent hears a person, that person's microphone sound, which it forwards unread to the speech service; and, when an agent the person lets see looks, one still picture of their camera, held in memory only until the agent has it.

Only members of the Keycloak group `REALM_LOGIN_GROUP` names use it. Why HopIn exists and what it deliberately is not: [docs/VISION.md](docs/VISION.md). How it is built, how a call runs and what it trusts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What a Call Offers

Up to 6 people see and hear each other in one call, share a screen, mute microphone and camera independently, chat line to line between the browsers, and lock the room against newcomers; a dropped connection keeps its place for a short while. A quiz master asks everyone five quiz questions and counts the right answers, and a registered app's table shows on the call's stage. The page is a SvelteKit build that `make frontend` writes and the server serves.

The ruled behaviour: the call in [docs/call.md](docs/call.md), the game master in [docs/game-master.md](docs/game-master.md), the stage in [docs/stage.md](docs/stage.md).

## Quick Start

You need Python 3.14+ and [uv](https://docs.astral.sh/uv/). Install with `make install`, set the required `HOPIN_` and `REALM_` settings and the two required `HOPIN_AGENT__` game settings and `REALM_AI_GROUP` from [docs/configuration.md](docs/configuration.md), and start the server with `make run`, which runs `hopin migrate` and then `hopin serve`; it listens on `HOPIN_HOST` and `HOPIN_PORT`.

Sign-in returns the person to `HOPIN_PUBLIC_ORIGIN`, which must be https, so open HopIn at that origin (behind a TLS-terminating proxy), not at the plain listen address. Without the required settings, or with one malformed, HopIn refuses to start and prints one line per setting naming its environment variable, never its value. `HOPIN_REDIS_URL` names a Redis that every HopIn process shares for its request budgets and for the signals that wake an agent's open listen; a local `redis-server` is enough, and HopIn has no in-memory fallback. `HOPIN_DATABASE_URL` names a Postgres (a local one is enough) whose schema `hopin migrate` brings to the image's head; `hopin serve` refuses to start on a database that is not at that head, naming the command, and `/health` is not ready while the database does not answer. Run `hopin migrate` before every start of a new version: it reads only the two database settings, concurrent runs wait for one another, and a run at head changes nothing.

Sign-in needs a Keycloak client whose redirect URI is `HOPIN_PUBLIC_ORIGIN` + `/auth/callback`; the settings a first start needs, and the rest of the client's setup, are in [docs/configuration.md](docs/configuration.md).

Press "Anruf starten" and share the generated link.

## Development

The [`Makefile`](Makefile) owns the targets for installing, running, checking and the end-to-end tests.

The frontend in `frontend/` needs Node 26 (24 also works) and pnpm, whose version `packageManager` in its `package.json` pins (Corepack installs it).

[AGENTS.md](AGENTS.md) is guidance for coding agents.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): context, building blocks, a call's runtime, deployment, trust
- [docs/game-master.md](docs/game-master.md): the game master's ruled behaviour
- [docs/configuration.md](docs/configuration.md): every setting, its default and its form
- [docs/person-data.md](docs/person-data.md): what HopIn holds about a person, its export, and what erasing a person removes and keeps
- [docs/call.md](docs/call.md): the call's ruled behaviour, pre-join to end screen
- [docs/stage.md](docs/stage.md): the stage on which a call shows a registered app's table, and the tests that prove it
- [docs/agent-endpoint.md](docs/agent-endpoint.md): how an agent reaches a call over MCP: who is admitted, each tool and what an agent reads
- [docs/runbooks/release.md](docs/runbooks/release.md): the release workflow and how its image reaches production

## License

MIT
