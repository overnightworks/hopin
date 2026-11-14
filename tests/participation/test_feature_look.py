"""CALL-63: a joined agent's look at its sponsor's camera, at participation's real feature and handler: the request to
the sponsor's browser, its answer in parts, the refusals and the bounds."""

import asyncio
import logging

import pytest
import pytest_asyncio
import structlog.testing

from hopin.config.constants import SignalType
from hopin.participation.vocabulary import (
    LOOK_ANSWER_SECONDS,
    LOOK_MAX_BYTES,
    LOOK_MAX_PARTS,
    LOOK_MAX_PIXELS,
    LOOK_PART_CHARACTERS,
    LOOK_WINDOW_SECONDS,
    LOOKS_PER_WINDOW,
    LookRefusal,
    ParticipationSignal,
)
from tests.participation.support.ends import (
    end_by_the_host,
    leave_by_the_agent,
    leave_by_the_sponsor,
    remove_by_the_host,
    shut_down,
)
from tests.participation.support.people import person_named, room_of
from tests.signaling.support.browsers import host_and_guest
from tests.support.pictures import jpeg_of, parts_of

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")
REQUEST = ParticipationSignal.AGENT_LOOK_REQUEST
LOOKED = ParticipationSignal.AGENT_LOOKED
SETTLE_TURNS = 20


async def settle():
    for _ in range(SETTLE_TURNS):
        await asyncio.sleep(0)


def seeing(agent_id, *, on):
    return {"type": ParticipationSignal.AGENT_SEEING.value, "agent_id": agent_id, "on": on}


def part(request_id, text, index=0, count=1):
    return {
        "type": ParticipationSignal.AGENT_LOOK_PART.value,
        "request_id": request_id,
        "index": index,
        "count": count,
        "data": text,
    }


def no_camera(request_id):
    return {"type": ParticipationSignal.AGENT_LOOK_NONE.value, "request_id": request_id}


def request_ids(browser):
    return [frame["request_id"] for frame in browser.frames(REQUEST)]


@pytest_asyncio.fixture
async def seen(link, participation):
    """A call of a host and a guest whose own agent is dialed in and may see the guest."""
    host, guest = await host_and_guest(link)
    agent = await participation.dial_in(GUEST, room_of(guest), "Claude")
    await guest.say(seeing(agent.agent_id, on=True))
    return host, guest, agent


@pytest.fixture
def looking(participation, seen):
    """Starts the agent's look and returns it once the sponsor's browser was asked."""
    _host, guest, _agent = seen

    async def start():
        look = asyncio.create_task(participation.look(GUEST, room_of(guest)))
        await settle()
        return look

    return start


async def answer_with(looking, guest, *messages):
    look = await looking()
    request_id = request_ids(guest)[-1]
    for message in messages:
        await guest.say(message(request_id))
    return await asyncio.wait_for(look, timeout=1)


class TestAPicture:
    async def test_call_63_the_sponsors_browser_alone_is_asked_and_its_picture_is_what_the_look_yields(
        self, seen, looking
    ):
        """CALL-63: A look asks the agent's sponsor's browser alone; the JPEG it sends back is the look's result."""
        host, guest, _agent = seen
        picture = jpeg_of()

        result = await answer_with(looking, guest, lambda request_id: part(request_id, parts_of(picture, 10**6)[0]))

        assert (result, len(request_ids(guest)), request_ids(host)) == (picture, 1, [])

    @pytest.mark.parametrize("order", [[0, 1, 2], [2, 0, 1]], ids=["in_order", "out_of_order"])
    async def test_call_63_a_picture_in_several_parts_is_assembled_by_their_indexes(self, seen, looking, order):
        """CALL-63: The picture comes in parts, each within a signaling frame, in whatever order they arrive."""
        _host, guest, _agent = seen
        picture = jpeg_of(size=3000)
        parts = parts_of(picture, 1400)
        assert len(parts) == 3

        result = await answer_with(
            looking,
            guest,
            *(lambda request_id, index=index: part(request_id, parts[index], index, 3) for index in order),
        )

        assert result == picture

    async def test_call_63_a_picture_at_the_bounds_is_taken(self, seen, looking):
        """CALL-63: A JPEG of 1280 pixels on a side and the most bytes a look holds is a picture."""
        _host, guest, _agent = seen
        picture = jpeg_of(LOOK_MAX_PIXELS, LOOK_MAX_PIXELS, LOOK_MAX_BYTES)
        parts = parts_of(picture, LOOK_PART_CHARACTERS)

        result = await answer_with(
            looking,
            guest,
            *(
                lambda request_id, index=index: part(request_id, parts[index], index, len(parts))
                for index in range(len(parts))
            ),
        )

        assert (result, len(parts) <= LOOK_MAX_PARTS) == (picture, True)

    async def test_call_63_every_seat_of_the_call_is_shown_a_mark_for_a_look_that_yielded_a_picture(
        self, seen, looking
    ):
        """CALL-63: The agent's tile shows a mark in every seat of the call at each look that yields a picture."""
        host, guest, agent = seen

        await answer_with(looking, guest, lambda request_id: part(request_id, parts_of(jpeg_of(), 10**6)[0]))

        assert [[frame["agent_id"] for frame in seat.frames(LOOKED)] for seat in (host, guest)] == [
            [agent.agent_id]
        ] * 2


class TestNoPicture:
    async def test_call_63_a_look_with_the_switch_off_is_refused_and_asks_nobody(self, participation, seen):
        """CALL-63: With the sponsor's seeing switch off, the look yields not_allowed and the browser is not asked."""
        host, guest, agent = seen
        await guest.say(seeing(agent.agent_id, on=False))

        result = await participation.look(GUEST, room_of(host))

        assert (result, request_ids(guest)) == (LookRefusal.NOT_ALLOWED, [])

    async def test_call_63_a_look_of_an_agent_that_is_not_joined_there_is_not_active(self, participation, seen):
        """CALL-63: A person with no joined agent in the room gets not_active."""
        host, _guest, _agent = seen

        assert await participation.look(person_named("host"), room_of(host)) is LookRefusal.NOT_ACTIVE

    async def test_call_63_a_browser_without_a_camera_answers_no_camera_and_no_mark_is_shown(self, seen, looking):
        """CALL-63: The sponsor's browser answers that it has no camera on: no_camera, and no look is shown."""
        host, guest, _agent = seen

        result = await answer_with(looking, guest, no_camera)

        assert (result, host.frames(LOOKED)) == (LookRefusal.NO_CAMERA, [])

    async def test_call_63_a_browser_that_does_not_answer_in_time_yields_no_answer(self, scheduler, looking):
        """CALL-63: A look the browser does not answer within the time yields no_answer."""
        look = await looking()

        await scheduler.advance(LOOK_ANSWER_SECONDS)

        assert await asyncio.wait_for(look, timeout=1) is LookRefusal.NO_ANSWER

    async def test_call_63_an_answer_after_the_time_is_dropped_and_shows_no_mark(self, scheduler, seen, looking):
        """CALL-63: A picture that comes after the look gave up is dropped."""
        host, guest, _agent = seen
        look = await looking()
        request_id = request_ids(guest)[-1]
        await scheduler.advance(LOOK_ANSWER_SECONDS)
        await look

        await guest.say(part(request_id, parts_of(jpeg_of(), 10**6)[0]))

        assert host.frames(LOOKED) == []

    async def test_call_63_the_switch_turned_off_while_the_browser_is_asked_yields_not_allowed(self, seen, looking):
        """CALL-63: Turning the switch off ends the looks that wait, and a picture that comes later is not shown."""
        host, guest, agent = seen
        look = await looking()
        request_id = request_ids(guest)[-1]

        await guest.say(seeing(agent.agent_id, on=False))
        await guest.say(part(request_id, parts_of(jpeg_of(), 10**6)[0]))

        assert (await look, host.frames(LOOKED)) == (LookRefusal.NOT_ALLOWED, [])

    @pytest.mark.parametrize(
        "end",
        [
            pytest.param(leave_by_the_agent, id="leave"),
            pytest.param(remove_by_the_host, id="removal"),
            pytest.param(leave_by_the_sponsor, id="the_sponsors_seat_leaving"),
            pytest.param(end_by_the_host, id="the_rooms_end"),
            pytest.param(shut_down, id="shutdown"),
        ],
    )
    async def test_call_63_every_end_of_the_agent_yields_not_active_to_a_look_that_waits(
        self, participation, seen, looking, end
    ):
        """CALL-63: A look that waits for the sponsor's browser yields nothing when the agent leaves, is removed, its
        sponsor goes, the call ends or the process shuts down."""
        host, guest, agent = seen
        look = await looking()

        await end(participation, host, guest, agent)

        assert await asyncio.wait_for(look, timeout=1) is LookRefusal.NOT_ACTIVE

    async def test_call_63_a_look_while_the_sponsors_seat_is_away_is_not_active_and_asks_nobody(
        self, participation, seen
    ):
        """CALL-63: A paused agent, whose sponsor's seat lost its socket, cannot look."""
        host, guest, _agent = seen
        await guest.drop(1006)

        result = await participation.look(GUEST, room_of(host))

        assert (result, request_ids(guest)) == (LookRefusal.NOT_ACTIVE, [])

    async def test_call_63_a_look_waiting_when_the_sponsors_seat_goes_away_yields_not_active(
        self, participation, seen, looking
    ):
        """CALL-63: A seat that drops while its browser is asked is not asked again and the look ends at once."""
        _host, guest, _agent = seen
        look = await looking()

        await guest.drop(1006)

        assert await asyncio.wait_for(look, timeout=1) is LookRefusal.NOT_ACTIVE


class TestTheCap:
    async def test_call_63_the_looks_a_window_holds_are_taken_and_the_next_one_waits_for_the_window(
        self, participation, scheduler, seen, looking
    ):
        """CALL-63: An agent looks LOOKS_PER_WINDOW times a window; one more is refused as too_fast and asks nobody,
        until the window has passed."""
        host, guest, _agent = seen
        for _ in range(LOOKS_PER_WINDOW):
            assert await answer_with(looking, guest, no_camera) is LookRefusal.NO_CAMERA
        asked = len(request_ids(guest))

        refused = await participation.look(GUEST, room_of(host))
        await scheduler.advance(LOOK_WINDOW_SECONDS)
        afterwards = await answer_with(looking, guest, no_camera)

        assert (refused, len(request_ids(guest)) - asked, afterwards) == (
            LookRefusal.TOO_FAST,
            1,
            LookRefusal.NO_CAMERA,
        )

    async def test_call_63_a_look_the_switch_refuses_is_not_counted(self, participation, seen, looking):
        """CALL-63: Only a look that asked the browser counts against the window."""
        host, guest, agent = seen
        await guest.say(seeing(agent.agent_id, on=False))
        for _ in range(LOOKS_PER_WINDOW):
            await participation.look(GUEST, room_of(host))
        await guest.say(seeing(agent.agent_id, on=True))

        assert await answer_with(looking, guest, no_camera) is LookRefusal.NO_CAMERA


class TestTheAnswers:
    async def test_call_63_an_answer_from_a_seat_other_than_the_sponsor_is_dropped(self, seen, looking):
        """CALL-63: Only the sponsor's seat answers a look, even with the request's id."""
        host, guest, _agent = seen
        look = await looking()
        request_id = request_ids(guest)[-1]

        await host.say(part(request_id, parts_of(jpeg_of(), 10**6)[0]))
        await host.say(no_camera(request_id))

        assert (look.done(), host.frames(LOOKED)) == (False, [])
        await guest.say(no_camera(request_id))

    async def test_call_63_an_answer_to_no_request_is_dropped(self, seen):
        """CALL-63: A picture for an unknown request is ignored."""
        host, guest, _agent = seen

        await guest.say(part("no-such-request", parts_of(jpeg_of(), 10**6)[0]))

        assert (host.frames(LOOKED), guest.frames(SignalType.ERROR)) == ([], [])


def unbounded_pictures():
    """Answers the sponsor's browser must not be taken at its word for, each as the messages it sends."""
    big = LOOK_MAX_PIXELS + 1
    heavy = jpeg_of(640, 480, LOOK_MAX_BYTES + 3)
    return {
        "too_wide": [lambda request_id: part(request_id, parts_of(jpeg_of(big, 720), 10**6)[0])],
        "too_high": [lambda request_id: part(request_id, parts_of(jpeg_of(720, big), 10**6)[0])],
        "no_height": [lambda request_id: part(request_id, parts_of(jpeg_of(720, 0), 10**6)[0])],
        "not_a_jpeg": [lambda request_id: part(request_id, "R0lGODlhAQABAAAAACw=")],
        "not_base64": [lambda request_id: part(request_id, "not base64 at all!")],
        "too_many_bytes": [
            lambda request_id, index=index, text=text: part(request_id, text, index, len(parts_of(heavy, 30000)))
            for index, text in enumerate(parts_of(heavy, 30000))
        ],
        "too_many_parts": [lambda request_id: part(request_id, "AAAA", 0, LOOK_MAX_PARTS + 1)],
        "a_part_too_long": [lambda request_id: part(request_id, "A" * (LOOK_PART_CHARACTERS + 4))],
        "a_part_beyond_the_count": [lambda request_id: part(request_id, "AAAA", 2, 2)],
        "a_negative_index": [lambda request_id: part(request_id, "AAAA", -1, 2)],
        "a_repeated_part": [
            lambda request_id: part(request_id, "AAAA", 0, 3),
            lambda request_id: part(request_id, "AAAA", 0, 3),
        ],
        "a_changed_count": [
            lambda request_id: part(request_id, "AAAA", 0, 3),
            lambda request_id: part(request_id, "AAAA", 1, 2),
        ],
    }


class TestTheBounds:
    @pytest.mark.parametrize("messages", list(unbounded_pictures().values()), ids=list(unbounded_pictures()))
    async def test_call_63_an_answer_outside_the_bounds_yields_unavailable_and_no_mark(self, seen, looking, messages):
        """CALL-63: A picture over 1280 pixels or the byte bound, one that is no JPEG or no base64, and parts that
        break the part bounds yield unavailable."""
        host, guest, _agent = seen

        result = await answer_with(looking, guest, *messages)

        assert (result, host.frames(LOOKED)) == (LookRefusal.UNAVAILABLE, [])


class TestNothingIsLogged:
    async def test_call_63_the_picture_reaches_no_log_record(self, caplog, seen, looking):
        """CALL-63: A look puts neither a part nor the picture in a log record."""
        _host, guest, _agent = seen
        picture = jpeg_of()
        text = parts_of(picture, 10**6)[0]

        with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as logs:
            await answer_with(looking, guest, lambda request_id: part(request_id, text))

        assert (text[:40] in caplog.text, text[:40] in repr(logs)) == (False, False)
