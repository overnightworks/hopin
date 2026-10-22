# HopIn

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![CI](https://github.com/overnightworks/hopin/actions/workflows/ci.yml/badge.svg)](https://github.com/overnightworks/hopin/actions/workflows/ci.yml)

HopIn is a self-hosted video call service. A signed-in member starts a call and shares its link, and friends join in the browser with nothing to install. The server only coordinates who connects to whom; audio and video flow directly between the browsers.

Only members of the Keycloak group `/apps/hopin` use it. How it is built, how a call runs and what it trusts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What a Call Offers

- **Up to 6 people** in one call, each seeing and hearing everyone else
- **Screen sharing**, switchable with the camera mid-call
- **Microphone and camera** muted independently, with a preview before joining
- **Chat** beside the video
- **Room lock**: the host locks the call from the More menu, so no new person joins; the host unlocks at any time
- **Reconnect**: a dropped connection keeps its place for a short while; the call waits for a dropped host before it ends
- **No frameworks** in the browser: plain HTML, CSS and ES modules

## Quick Start

You need Python 3.14+ and [uv](https://docs.astral.sh/uv/). Install with `make install`, set the four required settings from Configuration, and start the server with `make run`; it listens on `HOPIN_HOST` and `HOPIN_PORT`.

Sign-in returns the person to `HOPIN_PUBLIC_ORIGIN`, which must be https, so open HopIn at that origin (behind a TLS-terminating proxy), not at the plain listen address. Without the required settings HopIn refuses to start.

Press "Start a call" and share the generated link.

## Run in a Container

`compose.yaml` owns the container's keys: the settings it sets, the two secret files it mounts read-only, its pinned network, its restart policy and its healthcheck on `/health`. It publishes no host port and carries edge's routing label ([edge's README](https://github.com/overnightworks/edge#adding-a-tool-the-label-rule)); the `hal` commands are in [edge's README](https://github.com/overnightworks/edge#the-commands). Secret values never enter the environment.

## A Person's Data

HopIn holds a person's account (name and role), their sessions and their seats in a call only in memory; a restart forgets all of it. Members of `/apps/hopin/admin` (or a group below it) download what HopIn holds about one person in their signed-in browser at `/api/admin/people/<keycloak id>/export`, by the person's Keycloak id compared exactly. The answer is a ZIP holding `export.json`.

- **Listed:** the account's name and role, each session's start and end, and each seat's call link id and whether the person hosts that call. A seat that has not joined a call has no call link id.
- **Withheld, only counted:** session ids, refresh tokens and resume tokens (secrets), and the frames held for a resume in the person's own seats, which can carry other participants' names, chat lines, media state or connection descriptions.
- **Unknown id:** an id HopIn does not know answers an empty export with zero counts and creates nothing.
- **Not counted:** copies of the person's own frames in other participants' replay buffers (their name, chat lines, media state, connection descriptions), which stay until newer frames push them out and which HopIn cannot trace to the person once the seat has ended, and a call the person hosts during its 60-second grace.
- **Logs:** the `account_event` log lines carry the person's Keycloak id until the host's log period ends. They are described here and not part of the export.

The shape of `export.json` follows the fleet's shared people contract (`docs/people.md` in the edge repository). HopIn admits only an admin's signed-in browser session, not an access token, so hal cannot call the route yet.

## Configuration

`src/hopin/config/settings.py` owns the settings; every one is an environment variable.

| Variable | Default | Description |
|---|---|---|
| `HOPIN_HOST` | `0.0.0.0` | Server bind address |
| `HOPIN_PORT` | `8080` | Server port |
| `HOPIN_STUN_SERVER` | `stun:stun.cloudflare.com:3478` | STUN server for NAT traversal, served to the browser at `/api/ice-servers` |
| `HOPIN_MAX_PARTICIPANTS_PER_ROOM` | `6` | Maximum participants per call, from `2` to `6` |
| `HOPIN_OIDC_ISSUER` | required | The Keycloak realm URL, https, without a trailing slash |
| `HOPIN_OIDC_CLIENT_ID` | `hopin` | The Keycloak client HopIn signs in as |
| `HOPIN_OIDC_CLIENT_SECRET_FILE` | required | Absolute path of the file holding that client's secret |
| `HOPIN_PUBLIC_ORIGIN` | required | HopIn's own https origin, without a path or trailing slash |
| `HOPIN_SESSION_SECRET_FILE` | required | Absolute path of the file holding the session signing key, at least 32 characters |
| `HOPIN_TRUSTED_PROXIES` | empty | Comma-separated addresses and CIDR blocks of the proxies whose `X-Forwarded-For` names the person's address for the rate limits; empty trusts none, so every request counts against its direct peer |

HopIn refuses to start without the four required settings, or when a secret file cannot be read, and says which setting is at fault, never what a file holds.

## Development

| Command | For |
|---|---|
| `make install` | install all dependencies |
| `make run` | start the server |
| `make check` | lint, root layout, unit tests and dead code |
| `make test-all` | unit and integration tests |
| `make e2e` | browser end-to-end tests |

[AGENTS.md](AGENTS.md) is guidance for coding agents.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): context, building blocks, a call's runtime, deployment, trust
- [docs/decisions/](docs/decisions/): decision records
- [docs/mockups/](docs/mockups/): the blessed pictures a surface change starts from

## License

MIT
