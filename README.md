# HopIn

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![CI](https://github.com/overnightworks/hopin/actions/workflows/ci.yml/badge.svg)](https://github.com/overnightworks/hopin/actions/workflows/ci.yml)

Self-hosted video call service using WebRTC. Start a call, share a link, your friends join instantly in the browser. No accounts, no installs, and no third-party services except STUN through a configurable server (Cloudflare by default).

## How It Works

HopIn uses **WebRTC** with a **mesh topology** for peer-to-peer video calls:

1. You open the app, check your camera and microphone in a preview, and start the call — a room is created
2. You share the generated link (e.g. via WhatsApp)
3. Friends open the link, check their camera and microphone in the same preview, and join — their browser connects directly to yours
4. Everyone sends and receives video/audio to everyone else

The server only handles **signaling** (coordinating who connects to whom). The actual video/audio data flows directly between browsers via WebRTC.

```
┌──────────┐     signaling     ┌──────────┐
│ Browser A├────────────────────┤  Server  │
└────┬─────┘    (WebSocket)    └────┬─────┘
     │                              │
     │  video/audio (peer-to-peer)  │
     │                              │
┌────┴─────┐     signaling     ┌────┴─────┐
│ Browser B├────────────────────┤ Browser C│
└──────────┘                   └──────────┘
```

## Features

- **Video calls** with up to 6 participants (mesh topology)
- **Screen sharing** — switch between camera and screen mid-call
- **Mute/unmute** audio and video independently
- **Room locking** — host can lock/unlock the room to prevent new joins
- **No accounts** — create a room, share the link, done
- **Self-hosted** — runs on your machine, your data stays with you
- **Zero dependencies** in the browser — plain HTML/JS, no frameworks

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)

### Install and Run

```bash
git clone <your-repo-url> hopin
cd hopin
make install
make run
```

Set the four required settings (see Configuration) before `make run`: the https issuer and public origin, and the paths of the two secret files. Put no secret value in the environment. Without them HopIn refuses to start.

Once they are set, the server starts on `http://localhost:8080`.

- Open `http://localhost:8080/` and press "Start a call"
- Share the generated link with friends

### Run in a Container

```bash
docker compose up -d --build
```

The `hopin` service publishes no host port: it is reached through the edge reverse proxy by its
`edge.name: hopin` label, and answers on port 8080 inside its network. Its healthcheck calls `/health`.

The container needs the four required settings and the two secret files mounted read-only. `compose.yaml` does not carry them yet (go-live, #25 row 5), so the service refuses to start until it does.

### Sharing Over the Internet

Your PC is behind a router, so friends can't reach `localhost` directly. Options:

**Option 1: Tunnel (easiest, no router config)**

```bash
# Using ngrok
ngrok http 8080

# Using Cloudflare Tunnel
cloudflared tunnel --url http://localhost:8080
```

This gives you a public URL like `https://abc123.ngrok.io` that you share instead.

**Option 2: Port forwarding**

1. Forward port `8080` in your router settings to your PC's local IP
2. Use a free dynamic DNS service (e.g. [DuckDNS](https://www.duckdns.org/)) for a stable URL
3. Share `http://your-domain.duckdns.org:8080/`

## Room Security

HopIn gives the host (the account that started the call) one way to control access. The host is the account, not the connection: if the host's connection drops, the call waits 60 seconds for the host to come back, then ends for everyone. Anyone whose connection drops keeps their place for 30 seconds and picks it up again without the call noticing; a host who is not back within those 30 seconds is away for the remaining 30 of the 60.

### Room Locking

The host locks with the "Lock call" switch in the call's More menu. When locked:
- No new participants can join (the join link is refused as locked)
- Existing participants stay connected
- The host can **unlock** at any time (e.g. if a friend disconnects and needs to rejoin)

Both protections are enforced **server-side** — the server rejects unauthorized join attempts regardless of what the client sends.

## Configuration

All settings can be overridden via environment variables:

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

HopIn refuses to start without the four required settings, or when a secret file cannot be read, and says which setting is at fault, never what a file holds.

Example, with the four required settings already set as above:

```bash
HOPIN_PORT=3000 HOPIN_MAX_PARTICIPANTS_PER_ROOM=4 make run
```

## Development

### Commands

```bash
make install    # Install all dependencies
make lint       # Run ruff + mypy (strict mode)
make format     # Auto-fix formatting
make test       # Run unit tests with coverage
make test-all   # Run all tests (unit + integration)
make dead       # Run vulture dead code detection
make e2e        # Run the browser end-to-end tests (needs Chromium: uv run playwright install chromium)
make check      # Run all checks (lint + layout + test + dead)
make run        # Start the server
```

### Project Structure

```
src/hopin/
├── config/
│   ├── constants.py      # Application constants and enums
│   └── settings.py       # Environment-based configuration
├── rooms/
│   ├── models.py         # Room and Participant data models
│   └── manager.py        # Room lifecycle management
├── signaling/
│   ├── handler.py        # WebSocket signaling for WebRTC
│   ├── identity.py       # Who a socket is, resolved at the handshake
│   ├── replay.py         # Numbered log of the frames a seat missed while away
│   └── scheduler.py      # Timer port, injected so the host grace is testable
├── web/
│   ├── routes.py         # HTTP route definitions
│   └── static/
│       ├── index.html    # Video call page (markup only)
│       ├── login-failed.html  # Login failed card, ui-console's sign-in markup
│       └── assets/       # Served at /static
│           ├── css/
│           │   ├── ui-console.css  # Vendored verbatim from ui-console v0.3.1, loaded first
│           │   └── hopin.css
│           └── js/       # ES modules, app.js is the entry point
│               ├── app.js        # Wiring, call start and leave
│               ├── router.js     # The address and the screen, call links
│               ├── home.js       # Home screen
│               ├── prejoin.js    # Pre-join: preview, microphone and camera, Start or Join
│               ├── end.js        # End and refusal screens
│               ├── state.js      # Call and local-media state
│               ├── signaling.js  # WebSocket, resume, message handlers
│               ├── replay.js     # Numbered log of the frames sent, resent after a resume
│               ├── mesh.js       # Peer connections
│               ├── recovery.js   # ICE restart of failed connections
│               ├── bandwidth.js  # Upload cap by call size
│               ├── media.js      # Camera, microphone, screen share
│               ├── quality.js    # Round-trip indicator
│               ├── speaking.js   # Who is speaking, from the level of the arriving sound
│               ├── chat.js       # Chat panel
│               ├── media-state.js # Who is muted or has the camera off
│               ├── sounds.js     # Notification sounds
│               ├── stage.js      # The stage: tiles, grid by call size, picture-in-picture
│               ├── pip.js        # Dragging the phone picture-in-picture
│               ├── controls.js   # Control bar
│               ├── invite.js     # The call link: waiting card and Invite, Copy link, Share
│               ├── toast.js      # Short status messages
│               ├── status.js     # Call status, lock, participant count
│               └── dialogs.js    # Leave dialog
├── errors.py             # Custom exception hierarchy
├── app.py                # Application factory and wiring
└── main.py               # Entry point

tests/
├── unit/                 # Unit tests
└── integration/          # Integration tests
```

### Tech Stack

| Component | Technology |
|---|---|
| Server | FastAPI + uvicorn (async Python) |
| Real-time | WebSocket (signaling) + WebRTC (media) |
| Frontend | Vanilla HTML/JS |
| Type checking | mypy (strict mode) |
| Linting | ruff |
| Testing | pytest + pytest-cov |
| Dead code | vulture |
| Packaging | uv + hatchling |

### Signaling Protocol

Messages are JSON over WebSocket. The server identifies each socket by its webauth session at the handshake (the page's Origin must be HopIn's own) and names every peer with an id it mints, so peers address each other only by those ids. The server acts as a relay for connection setup:

| Signal | Direction | Purpose |
|---|---|---|
| `join` | Client → Server | Create or join a room; a seat joins at most one room, and a second `join` is answered `invalid_message` |
| `room_created` | Server → Client | Room created, here's the ID, with your own `participant_id` and the `resume_token` of your seat |
| `existing_participants` | Server → Client | List of peers already in the room, with your own `participant_id` and the `resume_token` of your seat |
| `resume` | Client → Server | First frame of a new socket: take back the seat `{participant_id, token, last_seq, sent_floor}` after a drop |
| `resumed` | Server → Client | The seat is yours again: the rotated `resume_token` and `last_seq`, the last client `seq` the server received |
| `participant_joined` | Server → Client | New peer joined your room |
| `participant_left` | Server → Client | Peer left your room |
| `host_away` | Server → Client | The host's last connection dropped: `seconds_left` until the call ends, `participant_id` of the dropped connection |
| `host_back` | Server → Client | The host returned within the wait: `participant_id` of the new connection |
| `media_state` | Client → Server → Client | my microphone and camera are on or off (`audio`, `video`); relayed to the rest of the room with `from`; not kept by the server |
| `end_room` | Client → Server | The host ends the call for everyone |
| `room_closed` | Server → Client | The call is over, with `reason`: `host_gone` (the wait ran out; the frame carries `grace_seconds`) or `ended` (the host ended it); after sending it the server forgets every seat of the room and closes its sockets, so a later `resume` is refused |
| `offer` | Client → Server → Client | WebRTC SDP offer, also an ICE restart (relayed to target) |
| `answer` | Client → Server → Client | WebRTC SDP answer (relayed to target) |
| `ice_candidate` | Client → Server → Client | ICE candidate (relayed to target) |
| `error` | Server → Client | a refusal or a bad frame, with its `reason` (the `full` refusal also carries `capacity`); `resume_refused` answers every failed `resume` alike and the client joins afresh |

Every server frame carries a `seq` and every client frame after a socket's first does too (`resume`, `resumed` and the `resume_refused` error aside, which belong to the new socket and carry none). Each side keeps its last 256 frames, so a resume replays what the other side missed in order, and a frame at or below the last `seq` received is ignored. A frame longer than 65536 characters is answered `invalid_message` and neither relayed nor kept. A newer `offer` or `answer` replaces the older descriptions and candidates to the same peer. The `resume_token` is a secret of the seat: it travels only in frames to its owner, changes with every resume, and is never logged.

### Coding Guidelines

See [AGENTS.md](AGENTS.md) for HopIn's own facts (stack, commands, tests, CI).

## Scaling Considerations

The mesh topology works well for **2–6 participants**. Each participant sends their stream to every other participant, so bandwidth usage grows quadratically.

For larger groups (6+), you would need to add an **SFU** (Selective Forwarding Unit) like [mediasoup](https://mediasoup.org/) or [Janus](https://janus.conf.meetecho.com/). The signaling server would remain largely the same — only the media routing changes.

## License

MIT
