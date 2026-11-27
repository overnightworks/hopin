# Configuration

This page lists every setting of HopIn, its default and its form, for the person who sets HopIn up.

Every setting is an environment variable with the `HOPIN_` prefix, except the nine `REALM_` keys, which name HopIn's objects in the Keycloak realm, the AI gateway's group and the speech scope under the same keys in every app. The settings classes own them: `src/hopin/config/settings.py` the database, server and realm settings, and the classes named below the table the game, speech, video, participation and stage ones. The client secret, the session signing key and the two TURN key files are the exception in content: the variables name files, HopIn reads them at start, and the secrets never enter the environment.

| Variable | Default | Description |
|---|---|---|
| `HOPIN_HOST` | `0.0.0.0` | Server bind address |
| `HOPIN_PORT` | `8080` | Server port |
| `LOG_LEVEL` | `info` | The root log level (`debug`, `info`, `warning`, `error`, `critical`); logline reads it without the `HOPIN_` prefix, and anything else stops the start |
| `SERVICE_VERSION` | unset; the release image sets it to the release version | The `version` of every log line, which logline reads without the `HOPIN_` prefix |
| `HOPIN_STUN_SERVER` | `stun:stun.cloudflare.com:3478` | STUN server for NAT traversal, served to the browser at `/api/ice-servers` |
| `HOPIN_TURN_PROVIDER` | `off` | `off` or `cloudflare`. With `cloudflare`, each call start asks the provider for a TURN credential that lives 12 hours and serves it with the STUN entry. Switching off (it takes a restart) stops new TURN credentials only: credentials already handed out stay valid at the provider until their 12 h end |
| `HOPIN_TURN_KEY_ID_FILE` | unset | Absolute path of the file holding the TURN key's ID; required when TURN is on |
| `HOPIN_TURN_API_TOKEN_FILE` | unset | Absolute path of the file holding the TURN key's API token; required when TURN is on |
| `HOPIN_MAX_PARTICIPANTS_PER_ROOM` | `6` | Maximum places per call, from `2` to `6`; people and joined agents take them alike |
| `HOPIN_OIDC_ISSUER` | required | The Keycloak realm URL, https, without a trailing slash |
| `HOPIN_OIDC_CLIENT_SECRET_FILE` | required | Absolute path of the file holding the secret of the client `REALM_CLIENT_ID` names |
| `HOPIN_PUBLIC_ORIGIN` | required | HopIn's own https origin, without a path or trailing slash |
| `HOPIN_SESSION_SECRET_FILE` | required | Absolute path of the file holding the session signing key, at least 32 characters |
| `HOPIN_TRUSTED_PROXIES` | empty | Comma-separated addresses and CIDR blocks of the proxies whose `X-Forwarded-For` names the person's address for the rate limits and the request log; empty trusts none, so every request counts against its direct peer |
| `REALM_CLIENT_ID` | required | The Keycloak client HopIn signs in as |
| `REALM_LOGIN_GROUP` | required | The Keycloak group path whose members sign in, such as `/apps/hopin`, compared exactly; a group below it admits too |
| `REALM_ADMIN_GROUP` | required | The Keycloak group path whose members administer, a group below it included; it lies below `REALM_LOGIN_GROUP` |
| `REALM_MCP_GROUP` | required | The Keycloak group path whose members' agents may reach `/mcp`, compared exactly |
| `REALM_MCP_SCOPE` | required | The Keycloak scope a token for `/mcp` carries |
| `REALM_AGENT_GROUP` | required | The Keycloak group path of agents' own accounts, which `/mcp` admits from any client |
| `REALM_MCP_CLIENT_ID` | required | The public Keycloak client a person's own agent signs in through |
| `HOPIN_REDIS_URL` | required | The `redis://` or `rediss://` URL of the Redis that every HopIn process shares. The per-address request budgets live there, so a budget spent through one process is refused through the others, and so do the signals that wake an agent's open listen. HopIn refuses to start without it and has no in-memory fallback; with Redis down `/health` is not ready and every budgeted route answers 503 |
| `HOPIN_DATABASE_URL` | required | The `postgresql://user@host:port/database` URL of the Postgres that every HopIn process shares; no password and no query parameter, which HopIn refuses. `hopin migrate` and `hopin serve` read it; the schema holds the accounts and the sessions, which every process reads and writes; an empty port (`host:/database`) is refused. With the database down `/health` is not ready and a request that needs a session answers 503 with `Retry-After`; every other database failure stays a 500 |
| `HOPIN_DATABASE_PASSWORD_FILE` | required | Absolute path of the file holding only the database user's password |
| `HOPIN_AGENT__GATEWAY__ORIGIN` | required while the game is installed | The gateway's http(s) origin (scheme, host and optional port) |
| `HOPIN_AGENT__GATEWAY__KEY_FILE` | required while the game is installed | Absolute path of the file holding HopIn's gateway key, read at every turn, so a missing or unusable file does not stop the start: the turn then reads as unavailable |
| `HOPIN_AGENT__GATEWAY__CA_FILE` | required for an HTTPS origin, refused for an HTTP one | Absolute path of the CA the gateway's HTTPS certificate is trusted by |
| `REALM_AI_GROUP` | required while the game is installed | The full Keycloak group path that admits a person to the game master, compared exactly, so a subgroup does not admit |
| `HOPIN_STAGE_APPS` | `[]` | The apps whose tables a call shows on its stage in a frame: a JSON list of `{"name", "title", "origin", "frame_path"}`. `name` is lowercase letters, digits and hyphens, a letter first, 32 characters at most, and stays stable, because it feeds the label of the app's person keys (`hallucinai/<name>/person-key/v1`) and renaming it empties every chair. `title` is what the page calls the app. `origin` is the app's https origin (scheme, lowercase ASCII host, optional port other than 443, no path); the page may frame exactly the listed origins. `frame_path` starts with `/`, holds `{context}` once and has no query, fragment or dot segment; a table's frame loads `origin` + `frame_path` with the table's key in place of `{context}` + `?mode=call`. Names and origins are unique, and no origin is `HOPIN_PUBLIC_ORIGIN`, else the start is refused; so does a malformed value, without echoing it. Empty means no stage. |
| `HOPIN_FIGURE_SOURCES` | `[]` | A JSON list of URL prefixes an agent with an account of its own may bring its figure from (each https, a lowercase host with no port or user, no query or fragment, and a path that starts and ends with `/` and holds only ASCII letters, digits and `-._~/`, with no empty, `.` or `..` segment); unset or `[]` means no figure is fetched and every agent shows its symbol. HopIn fetches a figure, each clip of a persona's gestures and a persona's room only from such a prefix, follows at most one redirect, only to https, and serves only bytes whose sha256 matches. An allowed prefix trusts its host's redirects for a figure, a clip and a room: an open redirect on an allowed host lets an agent make HopIn send one GET without credentials to another https address, whose answer it never sees. A malformed value stops the start, naming only the setting |
| `HOPIN_SPEECH__ORIGIN` | unset | The speech service's http origin (scheme, host and optional port, no path); unset means the game master stays silent |
| `HOPIN_SPEECH__VOICE` | `de_DE-thorsten-medium` | The speech service's catalogue voice the game master's lines are spoken with, and the one voice for an agent's sentences whose accept names no voice (an accept's voices, first choice first, take precedence: each sentence is spoken with the first of them the speech service's health offers ready, and written as a line when none is, and while an agent's voice is unavailable HopIn reads the health every 5 seconds to turn it available again); it must speak German, the page's default language, so a value outside the catalogue or a voice without German stops the start |
| `REALM_SPEECH_SCOPE` | required when the origin is set | The one Keycloak scope whose token the speech service admits (`speech-service-audience`); sign-in asks for it only with the voice on |
| `HOPIN_SPEECH__HEARING` | `false` | Whether an agent hears its sponsor, who sends their microphone sound through HopIn to the speech service; on needs the origin and the audience scope, or the start stops |
| `HOPIN_SPEECH__SPEAKING` | `false` | Whether an agent with an account of its own speaks in a call: HopIn renders its sentences through the speech service under the agent's own account and plays them to every seat; on needs the origin and the audience scope, or the start stops |
| `HOPIN_VIDEO__ORIGIN` | unset | The video service's http origin (scheme, host and optional port, no path); unset means no video code runs. With it set, TURN on and agents speaking, an agent whose accept lists a video look ahead of its figure look gets a video session on the service (CALL-81); no page shows it yet |
| `HOPIN_VIDEO__AUDIENCE_SCOPE` | required when the origin is set | The one Keycloak scope whose token the video service admits; HopIn exchanges an agent's token for it alone at the agent's accept, and the scope never enters a person's sign-in |

The `HOPIN_AGENT__` settings are read by the connector's `AgentSettings`, the `HOPIN_SPEECH__` ones by its `SpeechSettings` (which stays when the game is removed), the `HOPIN_VIDEO__` ones by its `VideoSettings`, and `HOPIN_FIGURE_SOURCES` by participation's `ParticipationSettings`, and `HOPIN_STAGE_APPS` by the stage's `StageSettings`; a missing or malformed one stops the start and echoes no input.

Agents dial in through `POST /mcp` with a bearer token and need no setting: the endpoint's audience is `HOPIN_PUBLIC_ORIGIN` + `/mcp`, and the realm decides who may use it (see [docs/agent-endpoint.md](agent-endpoint.md)). HopIn refuses to start without the required `HOPIN_` settings, any of the seven `REALM_` keys `RealmSettings` reads, which are all but `REALM_AI_GROUP` and `REALM_SPEECH_SCOPE` (a group key that is no group path included), or the required `HOPIN_AGENT__` settings, without the page build (`make frontend` writes it), when a secret file cannot be read, or when TURN is on and a key file is missing, empty or malformed, and says which setting is at fault, never what a file holds.

## Sign-in client

The Keycloak client sends the groups claim, and the account that signs in is in the group `REALM_LOGIN_GROUP` names.

## Verdict repeatability

`hopin verdict-repeatability [--runs N] [--minimum S]` asks the configured quiz master the same recorded reveals N times and reports how often its verdicts agree (GM-41 in `docs/game-master.md`); it reads only the `HOPIN_AGENT__` settings, costs real model calls, and the image carries it. `make verdict-repeatability RUNS=N` runs it from a checkout.
