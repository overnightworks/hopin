import pytest
import pytest_asyncio
import structlog.testing
from starlette.websockets import WebSocketDisconnect, WebSocketState

from hopin.config.constants import (
    MAX_LIVE_SEATS_PER_ACCOUNT,
    POLICY_VIOLATION_CLOSE_CODE,
    RESUME_WINDOW_SECONDS,
    SESSION_RECHECK_SECONDS,
    ErrorReason,
    SignalType,
)
from tests.signaling.support.browsers import Link, host_and_guest
from tests.support.signaling_fakes import account_named, disconnect_frame

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


def websocket_errors(logs):
    return [entry for entry in logs if entry["event"] == "websocket_error"]


def assert_resume_refused(browser):
    assert browser.frames(SignalType.RESUMED) == []
    assert browser.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.RESUME_REFUSED.value


class TestDropWithWriteInFlight:
    async def test_failed_send_before_the_drop_is_read_still_parks_the_seat_for_the_resume(self, link, scheduler):
        host, guest = await host_and_guest(link)
        fail_every_send_like_a_half_closed_socket(guest.socket)

        await host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "in flight"})
        await guest.drop(ABNORMAL_CLOSE_CODE)
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        await guest.resume()

        assert len(guest.frames(SignalType.RESUMED)) == 1


class TestServerClosesWhoseEchoIsLost:
    """The browser's close code can be lost; the server's own record decides, and the seat is not parked."""

    async def test_superseded_by_a_resume_leaves_the_seat_with_the_new_socket(self, link):
        host, guest = await host_and_guest(link)
        await guest.resume()
        old_socket = guest.sockets[0]

        with structlog.testing.capture_logs() as logs:
            old_socket.push(disconnect_frame(ABNORMAL_CLOSE_CODE))
            await link.tasks[old_socket]
        await host.say({"type": SignalType.OFFER.value, "target": guest.peer_id, "sdp": "for the new socket"})

        assert websocket_errors(logs) == []
        assert [frame["sdp"] for frame in guest.socket.sent if frame["type"] == SignalType.OFFER.value] == [
            "for the new socket"
        ]

    async def test_session_ended_removes_the_seat_without_parking_it(self, link, scheduler):
        _host, guest = await host_and_guest(link)
        guest.socket.account = None

        await scheduler.advance(SESSION_RECHECK_SECONDS)
        with structlog.testing.capture_logs() as logs:
            await guest.drop(ABNORMAL_CLOSE_CODE)
        await guest.resume()

        assert websocket_errors(logs) == []
        assert_resume_refused(guest)

    async def test_room_ended_removes_every_seat_without_parking_it(self, link):
        host, guest = await host_and_guest(link)

        await host.say({"type": SignalType.END_ROOM.value})
        with structlog.testing.capture_logs() as logs:
            await guest.drop(ABNORMAL_CLOSE_CODE)
        await guest.resume()

        assert websocket_errors(logs) == []
        assert_resume_refused(guest)

    async def test_beyond_the_call_cap_refuses_the_socket_and_holds_no_seat_for_it(self, link):
        account = account_named("busy")
        for _ in range(MAX_LIVE_SEATS_PER_ACCOUNT):
            await link.open(account)

        refused = await link.open(account)
        await link.tasks[refused]

        assert refused.closed_with == POLICY_VIOLATION_CLOSE_CODE
        assert refused.sent[0]["reason"] == ErrorReason.TOO_MANY_CALLS.value
