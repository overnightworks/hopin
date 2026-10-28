# HopIn

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![CI](https://github.com/overnightworks/hopin/actions/workflows/ci.yml/badge.svg)](https://github.com/overnightworks/hopin/actions/workflows/ci.yml)

HopIn is a self-hosted video call service. A signed-in member starts a call and shares its link, and friends join in the browser with nothing to install. The server signs people in and relays the setup messages and chat between the members of a call, but never media: audio and video flow directly between the browsers, or through the TURN relay when it is switched on and a direct path fails, still encrypted between the browsers.

Only members of the Keycloak group `/apps/hopin` use it. How it is built, how a call runs and what it trusts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What a Call Offers

- **Up to 6 people** in one call, each seeing and hearing everyone else
- **Screen sharing**, switchable with the camera mid-call
- **Microphone and camera** muted independently, with a preview before joining
- **Chat** beside the video
- **Room lock**: the host locks the call from the More menu, so no new person joins except the host's own account, which can return while there is room; the host unlocks at any time
- **Game master**: anyone in a call taps Start quiz in the More menu, and an AI asks everyone a quiz question in the chat, marked AI, and on a card above it; everyone types an answer on the card, which nobody else sees, and can change it until the reveal; all see how many have answered. An answer has a length cap. The reveal comes when everyone has answered or the starter taps Reveal; the game master names each answer and whether it was right, and the next question follows, five in all, then the game master's final score, which counts each person's right answers. The starter or the host ends a quiz early with End quiz in More, but not while the game master is thinking, and everyone then sees "Quiz ended."; if the game master fails after the first question, the quiz ends, the starter sees why and the others see "Quiz ended.". Someone who joins later plays from the next question. Every step runs on the starter's own AI access
- **Reconnect**: a dropped connection keeps its place for a short while; the call waits for a dropped host before it ends
- **No frameworks** in the browser: plain HTML, CSS and ES modules

## Quick Start

You need Python 3.14+ and [uv](https://docs.astral.sh/uv/). Install with `make install`, set the four required settings from Configuration, and start the server with `make run` (`hopin serve`); it listens on `HOPIN_HOST` and `HOPIN_PORT`.

Sign-in returns the person to `HOPIN_PUBLIC_ORIGIN`, which must be https, so open HopIn at that origin (behind a TLS-terminating proxy), not at the plain listen address. Without the required settings HopIn refuses to start.

Sign-in needs a Keycloak client whose redirect URI is `HOPIN_PUBLIC_ORIGIN` + `/auth/callback` and that sends the groups claim, and an account in `/apps/hopin`.

Press "Start a call" and share the generated link.

## Run in a Container

`compose.yaml` owns the container's keys: the settings it sets, the four secret files it mounts read-only (the client secret, the session signing key and the two TURN key files), its two networks (HopIn's own, pinned, and the back-channel network on which only Keycloak reaches it as `hopin.hallucinai.de`), its restart policy and its healthcheck on `/health`. It publishes no host port and carries edge's routing label ([edge's README](https://github.com/overnightworks/edge#adding-a-tool-the-label-rule)); the `hal` commands are in [edge's README](https://github.com/overnightworks/edge#the-commands). Secret values never enter the environment.

Production runs from edge's catalog: `hal apply` on a `vYYYY.MM.DD.N` tag of `master` publishes HopIn, and `hal stop hopin` stops it. Edge owns those commands, and [Deployment View](docs/ARCHITECTURE.md#4-deployment-view) says what runs.

## A Person's Data

HopIn holds a person's account (name and role), their sessions and their seats in a call only in memory; a restart forgets all of it. Members of `/apps/hopin/admin` (or a group below it) download what HopIn holds about one person in their signed-in browser at `/api/admin/people/<keycloak id>/export`, by the person's Keycloak id compared exactly. The answer is a ZIP holding `export.json`.

- **Listed:** the account's name and role, each session's start and end, and each seat's call link id and whether the person hosts that call. A seat that has not joined a call has no call link id.
- **Withheld, only counted:** session ids, refresh tokens, ID tokens and resume tokens (secrets), and the frames held for a resume in the person's own seats, which can carry other participants' names, chat lines, the game master's lines, media state or connection descriptions.
- **Unknown id:** an id HopIn does not know answers an empty export (empty `data`, `files` and `withheld`) and creates nothing.
- **Not counted:** copies of the person's own frames in other participants' replay buffers (their name, chat lines, media state, connection descriptions), which stay until newer frames push them out and which HopIn cannot trace to the person once the seat has ended, and a call the person hosts during its 60-second grace.
- **Logs:** the `account_event` log lines carry the person's Keycloak id until the host's log period ends. They are described here and not part of the export.

**The game master.** Start quiz is one run at the fleet's agent service and each reveal, tapped or automatic, two (the answer, then the next question or the final score), five questions and eleven runs in all, each made on the starter's account: HopIn spends the session's refresh token once for a fresh access token, keeps the rotated one in the session, and sends the quiz prompt, this quiz's turns so far (its requests and the game master's lines) and the request for the question, its answer or the final score, with that access token and HopIn's own workload token. Each reveal request carries every answer given to that question, each with the player's name as the call shows it, so the game master can name them and keep score, and every later run of that quiz carries them again; a player without an answer is not listed. A run sends no chat line and no media; the access token carries the starter's own account and name. Every line reaches everyone in the call; HopIn keeps the quiz in memory until it ends, and never logs a prompt, a line or what the service answered. The game's server side lives in `src/hopin/game/`, and `src/hopin/main.py` switches it in.

The shape of `export.json` follows the fleet's shared people contract (`docs/people.md` in the edge repository). HopIn admits only an admin's signed-in browser session, not an access token, so hal cannot call the route yet.

### Erasing a person

Remove the person from `/apps/hopin` in Keycloak and sign them out there. Keycloak's back-channel logout (`/auth/backchannel-logout`) then makes HopIn drop the person; a second logout for the same person, or one for a person HopIn does not know, changes nothing. Every back-channel logout does this, whatever started it, so an ordinary sign-out from Keycloak elsewhere also removes the account; the next sign-in creates it again.

- **Removed at once:** every session of the person, refresh and ID tokens included, and their account.
- **Removed within 60 seconds:** the person's own seats in calls, with the resume token and the frames held for it. A seat closes at the person's next frame or at the next minute check, or, for a seat whose connection dropped, when its 30-second wait ends. A call the person hosts ends for everyone after a further 60-second wait.
- **Stays until it ends by itself:** the person's answers and name in a running quiz, until the quiz ends; frames that already reached other participants (the person's name, chat lines, media state, and offers, answers and connection candidates, which carry network addresses) stay in those participants' replay buffers, at most 256 frames per seat, until newer frames push them out or that participant's seat ends. A call the person hosts keeps their account as its host until it ends. The `account_event` log lines (`user_created`, `role_changed`, `backchannel_logout`) keep the person's Keycloak id until the host's log period ends.

Afterwards the export shows no account and no sessions, and no seats once the calls have closed.

## Configuration

`src/hopin/config/settings.py` owns the settings; every one is an environment variable with the `HOPIN_` prefix. The client secret, the session signing key and the two TURN key files are the exception in content: the variables name files, HopIn reads them at start, and the secrets never enter the environment.

| Variable | Default | Description |
|---|---|---|
| `HOPIN_HOST` | `0.0.0.0` | Server bind address |
| `HOPIN_PORT` | `8080` | Server port |
| `HOPIN_STUN_SERVER` | `stun:stun.cloudflare.com:3478` | STUN server for NAT traversal, served to the browser at `/api/ice-servers` |
| `HOPIN_TURN_PROVIDER` | `off` | `off` or `cloudflare`. With `cloudflare`, each call start asks the provider for a TURN credential that lives 12 hours and serves it with the STUN entry. Switching off (it takes a restart) stops new TURN credentials only: credentials already handed out stay valid at the provider until their 12 h end |
| `HOPIN_TURN_KEY_ID_FILE` | unset | Absolute path of the file holding the TURN key's ID; required when TURN is on |
| `HOPIN_TURN_API_TOKEN_FILE` | unset | Absolute path of the file holding the TURN key's API token; required when TURN is on |
| `HOPIN_MAX_PARTICIPANTS_PER_ROOM` | `6` | Maximum participants per call, from `2` to `6` |
| `HOPIN_OIDC_ISSUER` | required | The Keycloak realm URL, https, without a trailing slash |
| `HOPIN_OIDC_CLIENT_ID` | `hopin` | The Keycloak client HopIn signs in as |
| `HOPIN_OIDC_CLIENT_SECRET_FILE` | required | Absolute path of the file holding that client's secret |
| `HOPIN_PUBLIC_ORIGIN` | required | HopIn's own https origin, without a path or trailing slash |
| `HOPIN_SESSION_SECRET_FILE` | required | Absolute path of the file holding the session signing key, at least 32 characters |
| `HOPIN_TRUSTED_PROXIES` | empty | Comma-separated addresses and CIDR blocks of the proxies whose `X-Forwarded-For` names the person's address for the rate limits; empty trusts none, so every request counts against its direct peer |
| `HOPIN_AGENT_SERVICE_URL` | `http://agent-service:8080` | The agent service's origin, without a path or trailing slash; the game master starts its runs at `/runs` there (read by the game's own settings) |

HopIn refuses to start without the four required settings, when a secret file cannot be read, or when TURN is on and a key file is missing, empty or malformed, and says which setting is at fault, never what a file holds.

## Development

| Command | For |
|---|---|
| `make install` | install all dependencies |
| `make run` | start the server |
| `make check` | lint, tests and dead code |
| `make e2e` | browser end-to-end tests |

[AGENTS.md](AGENTS.md) is guidance for coding agents.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): context, building blocks, a call's runtime, deployment, trust
- [docs/decisions/](docs/decisions/): decision records
- [docs/mockups/](docs/mockups/): the blessed pictures a surface change starts from

## License

MIT
