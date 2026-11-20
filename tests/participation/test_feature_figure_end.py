"""CALL-56: a seat reports why an agent's figure ended as a closed code, which the server writes down once."""

import pytest
import pytest_asyncio
import structlog.testing

from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.vocabulary import FigureEnd, ParticipationSignal
from hopin.rooms.reference import room_reference
from hopin.signaling.frames import ForeignFrame
from hopin.signaling.transition import RoomView, Transition
from tests.participation.support.people import person_named, room_of
from tests.signaling.support.browsers import host_and_guest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ENDED_EVENT = "agent_figure.ended"


def report(agent_id, code):
    return {"type": ParticipationSignal.AGENT_FIGURE_ENDED.value, "agent_id": agent_id, "code": code}


def endings_in(logs):
    return [entry for entry in logs if entry["event"] == ENDED_EVENT]


@pytest_asyncio.fixture
async def dialed(link, participation):
    """A call of a host and a guest with the guest's agent dialed in."""
    host, guest = await host_and_guest(link)
    agent = await participation.dial_in(person_named(guest.name), room_of(host), "Claude")
    return host, guest, agent


class TestAFigureEndReport:
    @pytest.mark.parametrize("code", list(FigureEnd))
    async def test_call_56_each_ending_is_logged_once_with_ids_and_its_code_only(self, dialed, code):
        host, guest, agent = dialed

        with structlog.testing.capture_logs() as logs:
            await guest.say(report(agent.agent_id, code.value))

        assert endings_in(logs) == [
            {
                "event": ENDED_EVENT,
                "room_ref": room_reference(room_of(host)),
                "agent_id": agent.agent_id,
                "code": code.value,
                "log_level": "info",
            }
        ]

    async def test_call_56_a_report_is_answered_with_no_frame(self, dialed):
        _, guest, agent = dialed
        sent_before = [len(socket.sent) for socket in guest.sockets]

        await guest.say(report(agent.agent_id, FigureEnd.LOST.value))

        assert [len(socket.sent) for socket in guest.sockets] == sent_before

    @pytest.mark.parametrize("code", ["exploded", "", None, 3])
    async def test_call_56_a_code_outside_the_closed_set_is_an_invalid_message_and_logs_nothing(self, dialed, code):
        _, guest, agent = dialed

        with structlog.testing.capture_logs() as logs:
            await guest.say(report(agent.agent_id, code))

        assert guest.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.INVALID_MESSAGE.value
        assert endings_in(logs) == []

    async def test_call_56_a_report_without_an_agent_id_is_an_invalid_message(self, dialed):
        _, guest, _ = dialed

        await guest.say({"type": ParticipationSignal.AGENT_FIGURE_ENDED.value, "code": FigureEnd.LOST.value})

        assert guest.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.INVALID_MESSAGE.value

    async def test_call_56_a_repeated_report_of_one_seat_for_one_agent_is_dropped(self, dialed):
        _, guest, agent = dialed

        with structlog.testing.capture_logs() as logs:
            await guest.say(report(agent.agent_id, FigureEnd.SLOW.value))
            await guest.say(report(agent.agent_id, FigureEnd.LOST.value))

        assert [entry["code"] for entry in endings_in(logs)] == [FigureEnd.SLOW.value]

    async def test_call_56_every_seat_of_the_room_is_logged_once_for_the_agent(self, dialed):
        host, guest, agent = dialed

        with structlog.testing.capture_logs() as logs:
            await guest.say(report(agent.agent_id, FigureEnd.LOST.value))
            await host.say(report(agent.agent_id, FigureEnd.REFUSED.value))

        assert [entry["code"] for entry in endings_in(logs)] == [FigureEnd.LOST.value, FigureEnd.REFUSED.value]

    async def test_call_56_a_report_for_an_agent_of_another_room_is_dropped(self, link, dialed):
        _, _, agent = dialed
        stranger = await link.join("stranger")

        with structlog.testing.capture_logs() as logs:
            await stranger.say(report(agent.agent_id, FigureEnd.LOST.value))

        assert endings_in(logs) == []
        assert stranger.frames(SignalType.ERROR) == []

    async def test_call_56_a_report_for_an_agent_that_is_not_joined_is_dropped(self, dialed):
        _, guest, _ = dialed

        with structlog.testing.capture_logs() as logs:
            await guest.say(report("no-such-agent", FigureEnd.LOST.value))

        assert endings_in(logs) == []
        assert guest.frames(SignalType.ERROR) == []

    async def test_call_56_a_report_for_an_agent_that_left_is_dropped(self, participation, dialed):
        host, guest, agent = dialed
        await participation.leave(person_named(guest.name), room_of(host))

        with structlog.testing.capture_logs() as logs:
            await guest.say(report(agent.agent_id, FigureEnd.LOST.value))

        assert endings_in(logs) == []

    async def test_call_56_a_seat_reporting_twice_for_an_agent_is_logged_once(self, link, dialed):
        host, _, agent = dialed
        visitor = await link.join("visitor", room_of(host))

        with structlog.testing.capture_logs() as logs:
            await visitor.say(report(agent.agent_id, FigureEnd.SLOW.value))
            await visitor.say(report(agent.agent_id, FigureEnd.LOST.value))

        assert [entry["code"] for entry in endings_in(logs)] == [FigureEnd.SLOW.value]


class TestAFigureEndReportAfterItsSeatLeft:
    async def test_call_56_a_seat_that_left_is_logged_again_when_the_same_seat_reports_anew(self, participation, dialed):
        host, guest, agent = dialed
        view = RoomView(room_id=room_of(host), members=(host.peer_id, guest.peer_id))
        frame = ForeignFrame(
            ParticipationSignal.AGENT_FIGURE_ENDED.value, {"agent_id": agent.agent_id, "code": FigureEnd.LOST.value}
        )
        participation.on_frame(view, guest.peer_id, frame, Transition())

        participation.seat_left(view, guest.peer_id, Transition())
        with structlog.testing.capture_logs() as logs:
            participation.on_frame(view, guest.peer_id, frame, Transition())

        assert len(endings_in(logs)) == 1
