"""The agent's `speak` call at the real `/mcp`, for the tests of the speaking tool; it sends the chosen headers, `_meta`
and arguments that a typed client never sends."""

from agents.member import SPEAK_TOOL, PlaceRef

from hopin.agent.tools import CALL_KIND

SENTENCE_SECONDS = 1.5
TEXT = "Ein geheimer Satz"


def speak(scene, text=TEXT, room_id=None, token=None, *, seconds=SENTENCE_SECONDS, headers=None, meta=None, **named):
    """The agent's `speak` call; `named` is what else the call names, such as its `language`, and `headers` and `meta`
    what else its request carries in its headers and in its `_meta`, such as its `traceparent`. The call is answered at
    the sentence's first chunk, so a sentence that plays has its end set once the stream has told its length, which
    this waits for, unless the sentence's stream breaks off, which `seconds` of `None` says."""
    place = PlaceRef(kind=CALL_KIND, id=scene.owner.room_id if room_id is None else room_id)
    dialed = scene.call.call(
        scene.token if token is None else token,
        SPEAK_TOOL,
        {"place": place.model_dump(), "text": text, **named},
        meta=meta,
        **(headers or {}),
    )
    if seconds is not None and dialed.outcome == {"spoken": True}:
        assert scene.call.scheduler.wait_for_timer(seconds), "the sentence's length never came"
    return dialed
