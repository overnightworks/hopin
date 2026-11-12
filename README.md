# HopIn

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![CI](https://github.com/overnightworks/hopin/actions/workflows/ci.yml/badge.svg)](https://github.com/overnightworks/hopin/actions/workflows/ci.yml)

HopIn is a self-hosted video call service. A signed-in member starts a call and shares its link, and friends join in the browser with nothing to install. The server signs people in and relays the setup messages and chat between the members of a call, but never media: audio and video flow directly between the browsers, or through the TURN relay when it is switched on and a direct path fails, still encrypted between the browsers.

Only members of the Keycloak group `/apps/hopin` use it. Why HopIn exists and what it deliberately is not: [docs/VISION.md](docs/VISION.md). How it is built, how a call runs and what it trusts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What a Call Offers

- **Up to 6 people** in one call, each seeing and hearing everyone else
- **Screen sharing**, switchable with the camera mid-call
- **Microphone and camera** muted independently, with a preview before joining
- **Chat** beside the video
- **Room lock**: the host locks the call from the "Mehr" menu, so no new person joins except the host's own account, which can return while there is room; the host unlocks at any time
- **Game master**: anyone in a call taps "Quiz starten" in the "Mehr" menu, and an AI asks everyone a quiz question in the chat, marked "KI", and on a card above it; everyone types an answer on the card, which nobody else sees, and can change it until the reveal; all see how many have answered. An answer has a length cap. The reveal comes when everyone has answered or the starter taps "Auflösen"; the game master names each answer and whether it was right, and the next question follows, five in all, then the game master's final score, which counts each person's right answers. The starter or the host ends a quiz early with "Quiz beenden" in "Mehr", but not while the game master is thinking, and everyone then sees "Quiz beendet."; if the game master fails after the first question, the quiz ends, the starter sees why and the others see "Quiz beendet.". If the starter leaves the call, or is gone longer than 30 seconds, the quiz ends and everyone else sees "Quiz beendet: <name> ist gegangen."; if someone else leaves, the count shrinks and the reveal comes once everyone still there has answered. Someone who joins later plays from the next question. Every step runs on the starter's own AI access
- **Skat table**: with `HOPIN_SKAT_ORIGIN` set, anyone in a call taps Show Skat table in the "Mehr" menu and pastes the link of a table at that origin; every seat, and everyone who joins later, then sees the table on the stage in a frame of Skat's own origin, and every video moves into a strip at the top. The frame runs scripts but may not use the camera, the microphone or screen capture, sends no referrer and cannot navigate the page. The person who brought the table or the host removes it with Remove table, and anyone does once the person who brought it has left; the table is gone when the call ends. HopIn keeps only which table a call shows, in memory, and logs no table id; the game itself, and Skat's own sign-in, are Skat's
- **Reconnect**: a dropped connection keeps its place for a short while; the call waits for a dropped host before it ends
- **A SvelteKit page** that `make frontend` builds; the server serves that build

## Quick Start

You need Python 3.14+ and [uv](https://docs.astral.sh/uv/). Install with `make install`, set the four required settings and the four required `HOPIN_AGENT__` game settings from Configuration, and start the server with `make run` (`hopin serve`); it listens on `HOPIN_HOST` and `HOPIN_PORT`.

Sign-in returns the person to `HOPIN_PUBLIC_ORIGIN`, which must be https, so open HopIn at that origin (behind a TLS-terminating proxy), not at the plain listen address. Without the required settings HopIn refuses to start.

Sign-in needs a Keycloak client whose redirect URI is `HOPIN_PUBLIC_ORIGIN` + `/auth/callback` and that sends the groups claim, and an account in `/apps/hopin`.

Press "Anruf starten" and share the generated link.

## Run in a Container

HopIn runs on the platform (k3s with Flux) from its attested private image in GHCR; the platform's repository owns its manifests and the live settings. [Deployment View](docs/ARCHITECTURE.md#4-deployment-view) says what runs. Two gaps hold there today ([platform#46](https://github.com/overnightworks/platform/issues/46)): the quiz master reads unavailable, and Keycloak's back-channel logout does not reach HopIn.

`compose.yaml` serves only the rollback path on hal. It owns the container's keys: the settings it sets, the four secret files it mounts read-only (the client secret, the session signing key and the two TURN key files), its two networks (HopIn's own, pinned, and the back-channel network on which only Keycloak reaches it as `hopin.hallucinai.de`), its restart policy and its healthcheck on `/health`. It publishes no host port and carries edge's routing label ([edge's README](https://github.com/overnightworks/edge#adding-a-tool-the-label-rule)); the `hal` commands are in [edge's README](https://github.com/overnightworks/edge#the-commands). Secret values never enter the environment.

A release is the `Release` workflow, which the operator dispatches with a version `YYYY.MM.DD.N`. It gates `main`, builds and attests the image in GHCR and tags `vYYYY.MM.DD.N`. The image ref reaches production in a platform pull request the operator merges. On the rollback path, edge's catalog applies instead: `hal apply` takes the newest `v…` tag and publishes HopIn, and `hal stop hopin` stops it. Edge owns those commands.

## A Person's Data

HopIn holds a person's account (name and role), their sessions and their seats in a call only in memory; a restart forgets all of it. Members of `/apps/hopin/admin` (or a group below it) download what HopIn holds about one person in their signed-in browser at `/api/admin/people/<keycloak id>/export`, by the person's Keycloak id compared exactly. The answer is a ZIP holding `export.json`.

- **Listed:** the account's name and role, each session's start and end, and each seat's call link id and whether the person hosts that call. A seat that has not joined a call has no call link id.
- **Withheld, only counted:** session ids, refresh tokens, ID tokens and resume tokens (secrets), and the frames held for a resume in the person's own seats, which can carry other participants' names, chat lines, the game master's lines, media state or connection descriptions.
- **Unknown id:** an id HopIn does not know answers an empty export (empty `data`, `files` and `withheld`) and creates nothing.
- **Not counted:** copies of the person's own frames in other participants' replay buffers (their name, chat lines, media state, connection descriptions), which stay until newer frames push them out and which HopIn cannot trace to the person once the seat has ended, and a call the person hosts during its 60-second grace.
- **Logs:** the `account_event` log lines carry the person's Keycloak id until the host's log period ends. They are described here and not part of the export.

**The game master.** "Quiz starten" is one run and each reveal, tapped or automatic, two (the answer, then the next question or the final score), five questions and eleven runs in all, each one chat completion at the fleet's LiteLLM gateway (`litellm-front`) with HopIn's own key, made for the starter: HopIn spends the session's refresh token once to read the starter's current groups from Keycloak, keeps the rotated one in the session, and starts the run only for a member of the API group (`HOPIN_AGENT__API_GROUP`); anyone else is told they have no access. The gateway receives the quiz prompt, this quiz's turns so far (its requests and the game master's lines), the request for the question, its answer or the final score, and the starter's fingerprint, a hash of their issuer and account id that keeps their budget apart. Each reveal request carries every answer given to that question, each with the player's name as the call shows it, so the game master can name them and keep score, and every later run of that quiz carries them again; a player without an answer is not listed. A run sends no chat line and no media. A person over budget reads as unavailable, the same as an outage. Every line reaches everyone in the call; HopIn keeps the quiz in memory until it ends, and never logs a prompt, a line or what the gateway answered. With `HOPIN_SPEECH__ORIGIN` set, each line is also spoken: per line HopIn sends the line's text (with players' names in a reveal and the final score) to the operator's speech service with the starter's access token, which costs one more refresh grant, and HopIn's own workload token, and serves the returned WAV unchanged to the seats that got the line; no token reaches a browser, and HopIn never logs the text, a token or the audio, only an outcome code. A line whose voice fails stays text. The game's server side lives in `src/hopin/game/` and its agent connector in `src/hopin/agent/`, and `src/hopin/main.py` switches it in.

The shape of `export.json` follows the fleet's shared people contract (`docs/people.md` in the edge repository). HopIn admits only an admin's signed-in browser session, not an access token, so hal cannot call the route yet.

### Erasing a person

Remove the person from `/apps/hopin` in Keycloak and sign them out there. Keycloak's back-channel logout (`/auth/backchannel-logout`, a route webauth serves) drops the person from HopIn. Today it does not reach HopIn on the platform ([platform#46](https://github.com/overnightworks/platform/issues/46)), so there the session lasts until its 12-hour lifetime ends. The steps below hold once the logout arrives. A second logout for the same person, or one for a person HopIn does not know, changes nothing. A logout that fails midway answers 400 and ends nothing. Every back-channel logout does this, whatever started it, so an ordinary sign-out from Keycloak elsewhere also removes the account; the next sign-in creates it again.

- **Removed at once, once the logout arrives:** every session of the person, refresh and ID tokens included, and their account.
- **Removed within 60 seconds:** the person's own seats in calls, with the resume token and the frames held for it. A seat closes at the person's next frame or at the next minute check, or, for a seat whose connection dropped, when its 30-second wait ends. A call the person hosts ends for everyone after a further 60-second wait.
- **Stays until it ends by itself:** the person's answers and name in a running quiz, until the quiz ends; frames that already reached other participants (the person's name, chat lines, media state, and offers, answers and connection candidates, which carry network addresses) stay in those participants' replay buffers, at most 256 frames per seat, until newer frames push them out or that participant's seat ends. A call the person hosts keeps their account as its host until it ends. The `account_event` log lines (`user_created`, `role_changed`, `backchannel_logout`) keep the person's Keycloak id until the host's log period ends.

Once the logout has arrived, the export shows no account and no sessions, and no seats once the calls have closed.

## A person's own agent

A person's own agent (first the operator's Claude Code) dials into a call the person sits in, through HopIn's MCP endpoint `POST /mcp` (the tools `join_call`, `leave_call`, `say` and, for an agent with an account of its own only, `accept_invitation`; `src/hopin/agent/mcp.py` and `src/hopin/participation/`). Everyone in the call then sees a tile with the name the person gave the agent, a "KI" badge and the person's name; the person or the host removes it. The agent joins only a call in which its person holds exactly one live seat, and it leaves when that seat leaves for good or when it calls `leave_call` with the call's id. It has no host or admin right. It writes a line in the chat with `say` and reads the chat from its join on through the resource of its call's window (below).

The bearer is accepted on this one path and nowhere else. webauth's gate admits a token whose audience is `HOPIN_PUBLIC_ORIGIN` + `/mcp` alone or exactly that and HopIn's own client id (any other extra audience is refused), the client `agents`, the scope `mcp-hopin` and membership of the Keycloak group `/apps/hopin/mcp`, for a person who has signed in once; the metadata of RFC 9728 is public at `/.well-known/oauth-protected-resource/mcp`. An agent's own account (group `/agents`, any client of the realm, created on its first admission) is admitted as `user` too. `join_call` serves only a person's own agent (an own account gets `invitation_only`), an own account reads the resource `member://standing` (`resources/read`; it wakes on a change through `subscriptions/listen`; the endpoint serves revision 2026-07-28 only; a person's own agent has no standing, and its read is an unknown resource): its whole standing, each place it is invited to, active in or paused in, and nothing for one it was removed from, and `accept_invitation(place, invitation, epoch)` (the runtime's shared arguments, the place's kind `call`) serves only an own account (a person's own agent gets `own_account_only`) and answers `{room, name}`, `leave_call(room)` serves both and acts only on a joined place, and `say(room, text)` serves both, answers `{}` and acts only on a joined place: every seat reads the line under the agent's name with the AI mark, 1 to 600 characters with no control character, at most 10 lines in 60 seconds, else `bad_line`, `too_fast` or (no joined place there, an invited agent included) `gone`, for the agent alone; no log line carries the text. Every agent joined in a call, of either kind, reads the call's chat from its join on at the resource that is `place_window_uri` of the place `call/<room>` (listed by `resources/list` for each call where the principal holds a joined agent, next to `member://standing` for an agent with an account of its own, and woken by `subscriptions/listen` when a line is added): a window of the newest 100 lines and at most 256 KiB of their text with the agent's own lines marked `own`, kept in memory only and gone with the agent's place (leave, removal, the sponsor's seat leaving for good, a lease's end, the call's end, shutdown). A person's own agent reads the places of its own person, never those of an agent it sponsors; any other room, an invited agent's room and a room left or ended is an unknown resource. The game's lines are not in the window. A member of the group `HOPIN_AGENT_ADDERS` names for the agent's owner adds the agent from their seat's "Mehr" while the owner holds exactly one seat in the same call; the call's host and each such member see the "Mehr" control even with no agent listed, which then says one sentence; the agent shows under the name its token carries, never its account's username, and leaves when its sponsor or the host removes it, when its owner's seat leaves for good, or when it has made no call for 120 seconds. The disclosure gate binds it as well, because it needs `/apps/hopin/mcp` too. The cookie session and its CSRF protection govern every other route, and the bearer opens neither `/api` nor `/ws`. Until the privacy text names the person's own AI agent and its provider as a recipient at the person's wish, nobody but the operator is put into `/apps/hopin/mcp`; that group is the disclosure gate, and the operator alone controls it. Bounds: one agent per seat, two per call, 32 per process; beyond them a join answers `busy`.

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
| `HOPIN_AGENT__BACKEND` | required while the game is installed | Which backend runs the game master's turns: `litellm`, the one value |
| `HOPIN_AGENT__GATEWAY__ORIGIN` | required while the game is installed | The gateway's http(s) origin (scheme, host and optional port), `http://litellm-front:4000` in `compose.yaml` |
| `HOPIN_AGENT__GATEWAY__KEY_FILE` | required while the game is installed | Absolute path of the file holding HopIn's gateway key, read at every turn, so a missing or unusable file does not stop the start: the turn then reads as unavailable |
| `HOPIN_AGENT__GATEWAY__CA_FILE` | required for an HTTPS origin, refused for an HTTP one | Absolute path of the CA the gateway's HTTPS certificate is trusted by |
| `HOPIN_AGENT__API_GROUP` | required while the game is installed | The full Keycloak group path that admits a person to the game master, compared exactly, so a subgroup does not admit |
| `HOPIN_SKAT_ORIGIN` | unset | Skat's https origin (scheme, lowercase ASCII host, optional port other than 443, no path), whose tables a call shows on its stage in a frame; the page may frame exactly this origin. Unset means no Skat. It must name another origin than `HOPIN_PUBLIC_ORIGIN`, else the start is refused, and a malformed value stops the start without echoing it |
| `HOPIN_AGENT_ADDERS` | `{}` | A JSON object that maps an owner's Keycloak subject to the full group path whose members may add that owner's agent with an account of its own, compared exactly; unset or `{}` means nobody adds, and a malformed value stops the start without echoing it |
| `HOPIN_FIGURE_SOURCES` | `[]` | A JSON list of URL prefixes an agent with an account of its own may bring its figure from (each https, a lowercase host with no port or user, no query or fragment, and a path that starts and ends with `/` and holds only ASCII letters, digits and `-._~/`, with no empty, `.` or `..` segment); unset or `[]` means no figure is fetched and every agent shows its symbol. HopIn fetches a figure only from such a prefix, follows at most one redirect, only to https, and serves only bytes whose sha256 matches. An allowed prefix trusts its host's redirects: an open redirect on an allowed host lets an agent make HopIn send one GET without credentials to another https address, whose answer it never sees. A malformed value stops the start, naming only the setting |
| `HOPIN_SPEECH__ORIGIN` | unset | The speech service's http origin (scheme, host and optional port, no path); unset means the game master stays silent |
| `HOPIN_SPEECH__VOICE` | `de_DE-thorsten-medium` | The speech service's catalogue voice the game master's lines are spoken with; a value outside the catalogue stops the start |
| `HOPIN_SPEECH__AUDIENCE_SCOPE` | required when the origin is set | The one Keycloak scope whose token the speech service admits (`speech-service-audience`); sign-in asks for it only with the voice on |

The `HOPIN_AGENT__` and `HOPIN_SPEECH__` settings are read by the connector's `AgentSettings`, and `HOPIN_AGENT_ADDERS` and `HOPIN_FIGURE_SOURCES` by participation's `ParticipationSettings`; a missing or malformed one stops the start and echoes no input.

Agents dial in through `POST /mcp` with a bearer token and need no setting, except that an agent with an account of its own is added only where `HOPIN_AGENT_ADDERS` maps its owner: the endpoint's audience is `HOPIN_PUBLIC_ORIGIN` + `/mcp`, and the realm decides who may use it (see "A person's own agent"). HopIn refuses to start without the four required settings and the required `HOPIN_AGENT__` settings, without the page build (`make frontend` writes it), when a secret file cannot be read, or when TURN is on and a key file is missing, empty or malformed, and says which setting is at fault, never what a file holds.

## Development

The [`Makefile`](Makefile) owns the targets for installing, running, checking and the end-to-end tests.

The frontend in `frontend/` needs Node 26 (24 also works) and pnpm, whose version `packageManager` in its `package.json` pins (Corepack installs it).

[AGENTS.md](AGENTS.md) is guidance for coding agents.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): context, building blocks, a call's runtime, deployment, trust
- [docs/game-master.md](docs/game-master.md): the game master's ruled behaviour and the tests that prove it
- [docs/call.md](docs/call.md): the call's ruled behaviour, pre-join to end screen, and the tests that prove it

## License

MIT
