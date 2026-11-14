"""CALL-63: the agent's `look` at HopIn's real `/mcp` and `/ws`: the picture it returns as an image with no text, its
refusals, and what no seat but the sponsor's is asked."""

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pytest
import structlog.testing
from agents.tools import ToolImage
from agents.turn import is_within_event_bound

from hopin.agent.tools import LOOK
from hopin.participation.vocabulary import (
    LOOK_ANSWER_SECONDS,
    LOOK_MAX_BYTES,
    LOOK_MAX_PIXELS,
    LOOK_PART_CHARACTERS,
    LOOKS_PER_WINDOW,
    ParticipationSignal,
)
from tests.participation.support.tool_calls import call_in
from tests.support.pictures import jpeg_of, parts_of

pytestmark = pytest.mark.integration

REQUEST = ParticipationSignal.AGENT_LOOK_REQUEST.value
LOOKED = ParticipationSignal.AGENT_LOOKED.value
SEEING = ParticipationSignal.AGENT_SEEING.value
ANSWER_SECONDS = 30


look = partial(call_in, tool=LOOK)


def part(request_id, text, index=0, count=1):
    return {
        "type": ParticipationSignal.AGENT_LOOK_PART.value,
        "request_id": request_id,
        "index": index,
        "count": count,
        "data": text,
    }


@pytest.fixture
def seeing_agent(joined_agent):
    """The scene whose agent the owner, its sponsor, let see."""
    owner = joined_agent.owner
    owner.send({"type": SEEING, "agent_id": joined_agent.agent_id, "on": True})
    while not owner.frame_of_type(SEEING)["on"]:
        pass
    return joined_agent


@pytest.fixture
def asked(seeing_agent):
    """A look on a worker thread of its own, since the call answers only when the sponsor's browser has; it returns the
    call's future and the id of the request the owner's seat was sent. The thread ends with the test."""
    pool = ThreadPoolExecutor(max_workers=1)

    def start():
        waiting = pool.submit(look, seeing_agent)
        return waiting, seeing_agent.owner.frame_of_type(REQUEST)["request_id"]

    yield start
    pool.shutdown(wait=True)


def refusal_of(dialed):
    return (dialed.is_error, dialed.outcome)


def content_of(dialed):
    return dialed.response.json()["result"]["content"]


def drained(scene):
    """Wait until both seats have read what the server sent before the host's lock, which reaches them both."""
    scene.owner.send({"type": "lock_room"})
    for seat in (scene.owner, scene.member):
        seat.frame_of_type("room_locked")


class TestThePicture:
    def test_call_63_the_look_returns_the_sponsors_picture_as_one_image_and_no_text(self, asked, seeing_agent):
        """CALL-63: The sponsor's picture comes back as the tool's only content: one image block of the JPEG."""
        picture = jpeg_of(1280, 720, 20000)
        waiting, request_id = asked()

        for index, text in enumerate(parts_of(picture, 6000)):
            seeing_agent.owner.send(part(request_id, text, index, len(parts_of(picture, 6000))))
        dialed = waiting.result(timeout=ANSWER_SECONDS)

        [block] = content_of(dialed)
        assert (dialed.is_error, block["type"], block["mimeType"], base64.b64decode(block["data"])) == (
            False,
            "image",
            "image/jpeg",
            picture,
        )

    def test_call_63_a_picture_of_the_most_a_look_holds_stays_within_the_connectors_result_cap(self):
        """CALL-63: The largest picture a look takes, as an image, is within the cap of a tool result."""
        largest = jpeg_of(LOOK_MAX_PIXELS, LOOK_MAX_PIXELS, LOOK_MAX_BYTES)

        assert is_within_event_bound("", ToolImage(content=largest))

    def test_call_63_the_marks_reach_every_seat_and_only_the_sponsor_was_asked(self, asked, seeing_agent):
        """CALL-63: Each seat is shown a look as a mark on the agent's tile; only the agent's sponsor is asked."""
        waiting, request_id = asked()
        seeing_agent.owner.send(part(request_id, parts_of(jpeg_of(), 10**6)[0]))
        waiting.result(timeout=ANSWER_SECONDS)

        drained(seeing_agent)

        for seat in (seeing_agent.owner, seeing_agent.member):
            assert [frame["agent_id"] for frame in seat.received if frame["type"] == LOOKED] == [seeing_agent.agent_id]
        assert [frame for frame in seeing_agent.member.received if frame["type"] == REQUEST] == []

    def test_call_63_the_picture_reaches_no_log_record(self, caplog, asked, seeing_agent):
        """CALL-63: A look is logged by its tool's name and outcome alone, never the picture."""
        text = parts_of(jpeg_of(), 10**6)[0]

        with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as logs:
            waiting, request_id = asked()
            seeing_agent.owner.send(part(request_id, text))
            waiting.result(timeout=ANSWER_SECONDS)

        assert (text[:40] in caplog.text, text[:40] in repr(logs)) == (False, False)


class TestRefusals:
    def test_call_63_with_the_switch_off_the_look_is_refused_and_nobody_is_asked(self, joined_agent):
        """CALL-63: The switch starts off, so a look without consent answers not_allowed."""
        dialed = look(joined_agent)
        drained(joined_agent)

        assert refusal_of(dialed) == (True, {"refused": "not_allowed"})
        assert [frame for frame in joined_agent.owner.received if frame["type"] == REQUEST] == []

    def test_call_63_a_browser_with_no_camera_is_refused_as_no_camera(self, asked, seeing_agent):
        """CALL-63: The sponsor's browser answering that no camera is on refuses the look as no_camera."""
        waiting, request_id = asked()

        seeing_agent.owner.send({"type": ParticipationSignal.AGENT_LOOK_NONE.value, "request_id": request_id})

        assert refusal_of(waiting.result(timeout=ANSWER_SECONDS)) == (True, {"refused": "no_camera"})

    def test_call_63_a_browser_that_does_not_answer_in_time_is_refused_as_no_answer(self, asked, seeing_agent):
        """CALL-63: A look with no answer in time is refused as no_answer."""
        waiting, _request_id = asked()

        seeing_agent.call.pass_time(LOOK_ANSWER_SECONDS)

        assert refusal_of(waiting.result(timeout=ANSWER_SECONDS)) == (True, {"refused": "no_answer"})

    def test_call_63_a_look_past_the_looks_a_minute_allows_is_refused_as_too_fast(self, asked, seeing_agent):
        """CALL-63: The looks a minute are bounded."""
        for _ in range(LOOKS_PER_WINDOW):
            waiting, request_id = asked()
            seeing_agent.owner.send({"type": ParticipationSignal.AGENT_LOOK_NONE.value, "request_id": request_id})
            waiting.result(timeout=ANSWER_SECONDS)

        dialed = look(seeing_agent)

        assert refusal_of(dialed) == (True, {"refused": "too_fast"})

    @pytest.mark.parametrize("picture_size", [(LOOK_MAX_PIXELS + 1, 10), (10, LOOK_MAX_PIXELS + 1)])
    def test_call_63_a_picture_over_1280_pixels_is_refused_as_unavailable(self, asked, seeing_agent, picture_size):
        """CALL-63: The browser is not trusted with the picture's size."""
        waiting, request_id = asked()

        seeing_agent.owner.send(part(request_id, parts_of(jpeg_of(*picture_size), LOOK_PART_CHARACTERS)[0]))

        assert refusal_of(waiting.result(timeout=ANSWER_SECONDS)) == (True, {"refused": "unavailable"})
