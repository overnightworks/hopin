"""What the tests of the agent's `speak` at the real `/mcp` and `/ws` share: the sound a fake speech service answers
with and the reads of what a call and its seats received."""

import asyncio

from hopin.participation.vocabulary import VOICE_PATH, ParticipationSignal
from tests.support.agent_speak import SENTENCE_SECONDS
from tests.support.speech_origin import silent_wav

VOICE = ParticipationSignal.AGENT_VOICE.value
LINE = ParticipationSignal.AGENT_LINE.value
SOUND = silent_wav(SENTENCE_SECONDS)
STREAMING_DATA_SIZE = 0xFFFFFFFF
CLIENT_CREDENTIALS_GRANT = "client_credentials"
ANSWER_SECONDS = 30


def still_waiting(scene, call):
    """Whether the call has no answer after the app's loop ran on past everything already due."""
    scene.call.client.portal.call(asyncio.sleep, 0)
    return not call.done()


def refusal_of(dialed):
    return (dialed.is_error, dialed.outcome)


def drained(scene):
    """Wait until both seats have read what the server sent before the host's lock, which reaches them both."""
    scene.owner.send({"type": "lock_room"})
    for seat in (scene.owner, scene.member):
        seat.frame_of_type("room_locked")


def frames_of(seat, signal_type):
    return [frame for frame in seat.received if frame["type"] == signal_type]


def sound_at(scene, seat, login=None):
    voice_id = seat.frame_of_type(VOICE)["voice_id"]
    return scene.call.voice(scene.member_login if login is None else login, f"{VOICE_PATH}/{voice_id}")


def grants(scene, grant_type):
    """The requests the issuer was asked to grant of this type, refused ones too."""
    return [request for request, _ in scene.call.issuer.exchanges if request.form["grant_type"] == grant_type]
