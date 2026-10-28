import json

import pytest
import pytest_asyncio
from starlette.websockets import WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    RESUME_WINDOW_SECONDS,
    SESSION_RECHECK_SECONDS,
    ErrorReason,
    SignalType,
)
from tests.signaling.support.browsers import Link, host_and_guest
from tests.support.signaling_fakes import disconnect_frame

ABNORMAL_CLOSE_CODE = 1006

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


def fail_every_send_like_a_half_closed_socket(socket):
    """Starlette marks the socket disconnected when a write fails, before the receive loop reads the drop."""

    async def failing_send_text(text):
        socket.application_state = WebSocketState.DISCONNECTED
        raise WebSocketDisconnect(ABNORMAL_CLOSE_CODE)

    socket.send_text = failing_send_text


def offer_to(browser, sdp):
    return {"type": SignalType.OFFER.value, "target": browser.peer_id, "sdp": sdp}


def assert_resume_refused(browser):
    assert browser.frames(SignalType.RESUMED) == []
    assert browser.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.RESUME_REFUSED.value


class TestDropWithWriteInFlight:
    async def test_failed_send_before_the_drop_is_read_still_parks_the_seat_for_the_resume(self, link, scheduler):
        host, guest = await host_and_guest(link)
        fail_every_send_like_a_half_closed_socket(guest.socket)

        await host.say(offer_to(guest, "in flight"))
        await guest.drop(ABNORMAL_CLOSE_CODE)
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        await guest.resume()

        assert len(guest.frames(SignalType.RESUMED)) == 1


class TestServerClosesWhoseEchoIsLost:
    """The browser's close code can be lost. In each of these closes the server removes the seat, or hands it
    to the resuming socket, before it closes the socket, so a drop read for that socket finds no seat to park."""

    async def test_superseded_by_a_resume_leaves_the_seat_with_the_new_socket(self, link):
        host, guest = await host_and_guest(link)
        await guest.resume()
        old_socket = guest.sockets[0]

        old_socket.push(disconnect_frame(ABNORMAL_CLOSE_CODE))
        await link.tasks[old_socket]
        await host.say(offer_to(guest, "for the new socket"))

        assert len(link.handler.seats_of(guest.name)) == 1
        assert [frame["sdp"] for frame in guest.socket.sent if frame["type"] == SignalType.OFFER.value] == [
            "for the new socket"
        ]

    async def test_session_ended_removes_the_seat_without_parking_it(self, link, scheduler):
        _host, guest = await host_and_guest(link)
        guest.socket.account = None

        await scheduler.advance(SESSION_RECHECK_SECONDS)
        await guest.drop(ABNORMAL_CLOSE_CODE)
        seats_after_the_drop = link.handler.seats_of(guest.name)
        await guest.resume()

        assert seats_after_the_drop == ()
        assert_resume_refused(guest)

    async def test_room_ended_removes_every_seat_without_parking_it(self, link):
        host, guest = await host_and_guest(link)

        await host.say({"type": SignalType.END_ROOM.value})
        await guest.drop(ABNORMAL_CLOSE_CODE)
        seats_after_the_drop = link.handler.seats_of(guest.name)
        await guest.resume()

        assert seats_after_the_drop == ()
        assert_resume_refused(guest)


class TestRoomEndRacingTheDrop:
    """The room's end writes its last frame through each seat before it removes the seat, but the room
    membership a park needs is gone before that first write."""

    async def test_drop_read_while_the_room_closed_frame_is_written_neither_parks_nor_lets_the_seat_resume(self, link):
        host, guest = await host_and_guest(link)
        write = guest.socket.send_text
        during_the_room_end = {}

        async def drop_and_resume_after_the_room_closed_frame(text):
            await write(text)
            if json.loads(text)["type"] == SignalType.ROOM_CLOSED.value:
                await guest.drop(ABNORMAL_CLOSE_CODE)
                during_the_room_end["seats"] = link.handler.seats_of(guest.name)
                await guest.resume()

        guest.socket.send_text = drop_and_resume_after_the_room_closed_frame
        await host.say({"type": SignalType.END_ROOM.value})

        assert during_the_room_end["seats"] == ()
        assert_resume_refused(guest)
