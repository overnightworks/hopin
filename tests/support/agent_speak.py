"""The agent's `speak` call at the real `/mcp`, for the tests of the speaking tool."""

from agents.member import SPEAK_TOOL, PlaceRef

from hopin.agent.tools import CALL_KIND

SENTENCE_SECONDS = 1.5
TEXT = "Ein geheimer Satz"


def speak(scene, text=TEXT, room_id=None, token=None, **named):
    """The agent's `speak` call; `named` is what else the call names, such as its `language`."""
    place = PlaceRef(kind=CALL_KIND, id=scene.owner.room_id if room_id is None else room_id)
    return scene.call.call(
        scene.token if token is None else token, SPEAK_TOOL, {"place": place.model_dump(), "text": text, **named}
    )
