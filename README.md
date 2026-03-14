# HopIn

Self-hosted video call service using WebRTC. Start a call, share a link, your friends join instantly in the browser. No accounts, no installs, no third-party services.

## How It Works

HopIn uses **WebRTC** with a **mesh topology** for peer-to-peer video calls:

1. You open the app and start a call — a room is created
2. You share the generated link (e.g. via WhatsApp)
3. Friends click the link — their browser connects directly to yours
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
- **Room passwords** — optionally protect calls with a password
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

The server starts on `http://localhost:8080`.

- Open `http://localhost:8080/call` to start a new call
- Share the generated link with friends

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
3. Share `http://your-domain.duckdns.org:8080/call`

## Room Security

HopIn gives the host (the person who created the call) two ways to control access:

### Password Protection

When starting a call, you can optionally set a password. Anyone who clicks the join link will be prompted to enter the password before they can join.

### Room Locking

The host sees a **Lock Room** button in the controls bar. When locked:
- No new participants can join (even with the correct password)
- Existing participants stay connected
- The host can **unlock** at any time (e.g. if a friend disconnects and needs to rejoin)

Both protections are enforced **server-side** — the server rejects unauthorized join attempts regardless of what the client sends.

## Configuration

All settings can be overridden via environment variables:

| Variable | Default | Description |
|---|---|---|
| `HOPIN_HOST` | `0.0.0.0` | Server bind address |
| `HOPIN_PORT` | `8080` | Server port |
| `HOPIN_STUN_SERVER` | `stun:stun.l.google.com:19302` | STUN server for NAT traversal |
| `HOPIN_MAX_PARTICIPANTS_PER_ROOM` | `6` | Maximum participants per call |

Example:

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
make check      # Run all checks (lint + test + dead)
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
│   └── handler.py        # WebSocket signaling for WebRTC
├── web/
│   ├── routes.py         # HTTP route definitions
│   └── static/
│       └── call.html     # Video call frontend (single page)
├── errors.py             # Custom exception hierarchy
├── app.py                # Application factory and wiring
└── main.py               # Entry point

tests/
├── unit/                 # Unit tests (104 tests, 90% coverage)
└── integration/          # Integration tests
```

### Tech Stack

| Component | Technology |
|---|---|
| Server | aiohttp (async Python) |
| Real-time | WebSocket (signaling) + WebRTC (media) |
| Frontend | Vanilla HTML/JS |
| Type checking | mypy (strict mode) |
| Linting | ruff |
| Testing | pytest + pytest-cov |
| Dead code | vulture |
| Packaging | uv + hatchling |

### Signaling Protocol

Messages are JSON over WebSocket. The server acts as a relay for connection setup:

| Signal | Direction | Purpose |
|---|---|---|
| `join` | Client → Server | Create or join a room |
| `room_created` | Server → Client | Room created, here's the ID |
| `existing_participants` | Server → Client | List of peers already in the room |
| `participant_joined` | Server → Client | New peer joined your room |
| `participant_left` | Server → Client | Peer left your room |
| `offer` | Client → Server → Client | WebRTC SDP offer (relayed to target) |
| `answer` | Client → Server → Client | WebRTC SDP answer (relayed to target) |
| `ice_candidate` | Client → Server → Client | ICE candidate (relayed to target) |

### Coding Guidelines

See [CLAUDE.md](CLAUDE.md) for the full coding standards. Key principles:

- Self-explaining code, no inline comments unless explaining *why*
- No hardcoded strings — constants and config only
- No god classes — single responsibility per class
- Dependency injection throughout
- TDD with 100% coverage on business logic
- mypy strict mode, ruff linting, vulture dead code detection

## Scaling Considerations

The mesh topology works well for **2–6 participants**. Each participant sends their stream to every other participant, so bandwidth usage grows quadratically.

For larger groups (6+), you would need to add an **SFU** (Selective Forwarding Unit) like [mediasoup](https://mediasoup.org/) or [Janus](https://janus.conf.meetecho.com/). The signaling server would remain largely the same — only the media routing changes.

## License

MIT
