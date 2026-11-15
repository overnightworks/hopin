"""What the hearing route's tests share: the hearing's scope, the agent's window read through the real `/mcp`, and the
endings the route logs."""

import json
from contextlib import contextmanager

import structlog.testing
from agents.member import LEASE_SECONDS

AUDIENCE_SCOPE = "speech-audience"


def entries_of(scene, token=None, room_id=None):
    answer = scene.call.window(
        scene.token if token is None else token, scene.owner.room_id if room_id is None else room_id
    )
    return json.loads(answer["result"]["contents"][0]["text"])["entries"]


def header_of(session, name):
    return next(value for header, value in session.headers.items() if header.lower() == name.lower())


def lose_the_held_token(scene):
    """The agent keeps calling within its lease, the issuer refuses each new exchange, and the token it held lapses."""
    scene.call.issuer.refuses_token_exchange_grants = True
    for _ in range(3):
        scene.call.pass_time(LEASE_SECONDS - 1)
        scene.standings()


@contextmanager
def logged_endings():
    """The outcomes the route logs as a hop's ending, filled while the block runs."""
    endings = []

    def note_the_ending(_logger, _method, event):
        if event["event"] == "agent_hearing_ended":
            endings.append(event["outcome"])
        return event

    with structlog.testing.capture_logs(processors=[note_the_ending]):
        yield endings
