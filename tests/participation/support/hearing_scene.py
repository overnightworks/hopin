"""What the hearing route's tests share: the hearing's scope, the agent's window read through the real `/mcp`, the
endings the route logs and a sentence the service finished."""

import json
import secrets

from agents.member import LEASE_SECONDS

from hopin.participation.vocabulary import ParticipationSignal
from tests.support.fake_speech import a_sentence
from tests.support.hearing_socket import SOUND, closed_with, hop

AUDIENCE_SCOPE = "speech-audience"


async def heard(participation, seat, text, began_at=0.0):
    """The speech service's finished sentence of the seat, under an item id of its own."""
    await participation.heard(seat, text, began_at=began_at, item_id=f"item_{secrets.token_hex(4)}")


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


def endings_of(lines):
    """How each hop ended, as the line of its ending says: the reason of a hop that served nobody, else the outcome."""
    return [line.get("reason", line["outcome"]) for line in lines if line["event"] == "hearing.ended"]


def switch_off(scene):
    scene.owner.send({"type": ParticipationSignal.AGENT_HEARING.value, "agent_id": scene.agent_id, "on": False})


def hear_one_sentence(scene, fake_speech, text="Guten Tag", *, session_number=1, **sentence):
    """A hop in which the service hears one sentence and ends in order; the code the browser's socket closed with.
    The hop is the service's `session_number`-th session."""
    with hop(scene) as socket:
        socket.send_bytes(SOUND)
        session = fake_speech.next_session(session_number)
        for event in a_sentence(text, **sentence):
            session.say(event)
        session.hang_up()
        return closed_with(socket)
