import asyncio

import pytest

from hopin.config.constants import RESUME_WINDOW_SECONDS, SESSION_RECHECK_SECONDS, SignalType
from hopin.participation.feature import DialedAgent
from hopin.participation.vocabulary import (
    AGENT_NAME_MAX_CHARACTERS,
    AGENTS_PER_PROCESS,
    AGENTS_PER_ROOM,
    AgentRefusal,
    ParticipationSignal,
)
from tests.participation.conftest import person_named, room_of
from tests.signaling.support.browsers import HeldClose, host_and_guest

pytestmark = pytest.mark.integration

NAME = "Claude"


def agent_frames(browser, signal):
    return [frame for socket in browser.sockets for frame in socket.sent if frame["type"] == signal.value]


def without_seq(frames):
    return [{key: value for key, value in frame.items() if key != "seq"} for frame in frames]


async def dial(participation, browser, name=NAME):
    return await participation.dial_in(person_named(browser.name), room_of(browser), name)


@pytest.mark.asyncio
class TestJoiningOneLiveSeat:
    async def test_call_48_an_agent_joins_the_call_its_person_sits_in_and_everyone_sees_its_tile(
        self, link, participation
    ):
        """CALL-48: An agent joins the call its person sits in, and the room's seats are told of its tile."""
        host, guest = await host_and_guest(link)

        agent = await dial(participation, guest)

        assert isinstance(agent, DialedAgent)
        expected = {
            "type": ParticipationSignal.AGENT_JOINED.value,
            "agent_id": agent.agent_id,
            "name": NAME,
            "sponsor": "guest",
            "sponsor_id": guest.peer_id,
        }
        for browser in (host, guest):
            assert without_seq(agent_frames(browser, ParticipationSignal.AGENT_JOINED)) == [expected]

    async def test_call_48_a_person_without_a_seat_in_the_room_gets_no_seat(self, link, participation):
        """CALL-48: A person with no seat in the room is refused with no_seat."""
        host = await link.join("host")
        await link.join("stranger")

        assert await participation.dial_in(person_named("stranger"), room_of(host), NAME) is AgentRefusal.NO_SEAT

    async def test_call_48_a_person_with_two_seats_in_the_room_gets_two_seats(self, link, participation):
        """CALL-48: A person with two seats in the room is refused with two_seats."""
        host = await link.join("host")
        await link.join("host", room_of(host))

        assert await dial(participation, host) is AgentRefusal.TWO_SEATS

    async def test_call_48_the_seats_of_one_person_in_two_rooms_hold_two_independent_agents(self, link, participation):
        """CALL-48: One person's seats in two rooms hold two agents with two ids."""
        first = await link.join("host")
        second = await link.join("host")

        agents = [await dial(participation, first), await dial(participation, second)]

        assert agents[0].agent_id != agents[1].agent_id
        assert [agent.room_id for agent in agents] == [room_of(first), room_of(second)]

    async def test_call_48_a_repeat_dial_in_returns_the_same_agent_under_its_first_name_and_tells_nobody_again(
        self, link, participation
    ):
        """CALL-48: A repeat dial-in returns the same agent id and keeps the first name."""
        host = await link.join("host")

        first = await dial(participation, host, "First")
        again = await dial(participation, host, "Second")

        assert (again.agent_id, again.name) == (first.agent_id, "First")
        assert len(agent_frames(host, ParticipationSignal.AGENT_JOINED)) == 1

    async def test_call_48_a_late_joiner_sees_the_agents_already_in_the_room(self, link, participation):
        """CALL-48: A late joiner is told of the agents already in the room."""
        host = await link.join("host")
        agent = await dial(participation, host)

        late = await link.join("late", room_of(host))

        [joined] = agent_frames(late, ParticipationSignal.AGENT_JOINED)
        assert (joined["agent_id"], joined["name"], joined["sponsor"]) == (agent.agent_id, NAME, "host")

    async def test_call_48_a_parked_seat_keeps_its_agent_whose_calls_answer_sponsor_away_until_it_resumes(
        self, link, participation
    ):
        """CALL-48: A parked seat keeps its agent; calls answer sponsor_away, and after the resume they work."""
        host, guest = await host_and_guest(link)
        agent = await dial(participation, guest)

        await guest.drop()
        parked = [await dial(participation, guest), await participation.leave(person_named("guest"), agent.agent_id)]
        await guest.resume()
        resumed = await participation.leave(person_named("guest"), agent.agent_id)

        assert parked == [AgentRefusal.SPONSOR_AWAY, AgentRefusal.SPONSOR_AWAY]
        assert resumed is None
        assert agent_frames(host, ParticipationSignal.AGENT_LEFT) != []

    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param("leaves", id="a leave"),
            pytest.param("grace", id="a grace that ran out"),
            pytest.param("session", id="an ended session"),
            pytest.param("refused_resume", id="a resume that is refused"),
        ],
    )
    async def test_call_48_the_agent_leaves_when_its_seat_leaves_for_good(self, link, participation, scheduler, ending):
        """CALL-48: The agent leaves when its seat leaves for good, and every seat is told once."""
        host, guest = await host_and_guest(link)
        agent = await dial(participation, guest)

        match ending:
            case "leaves":
                await guest.drop(code=1000)
            case "grace":
                await guest.drop()
                await scheduler.advance(RESUME_WINDOW_SECONDS)
            case "session":
                guest.socket.account = None
                await scheduler.advance(SESSION_RECHECK_SECONDS)
                await guest.drop()
            case "refused_resume":
                await guest.drop()
                await guest.resume(sent_floor=1)

        assert [frame["agent_id"] for frame in agent_frames(host, ParticipationSignal.AGENT_LEFT)] == [agent.agent_id]
        assert await participation.leave(person_named("guest"), agent.agent_id) is AgentRefusal.GONE

    async def test_call_48_a_call_made_while_its_seat_is_leaving_finds_the_agent_gone(
        self, link, participation, scheduler
    ):
        """CALL-48: A call that meets an agent whose seat has just left is answered gone."""
        _host, guest = await host_and_guest(link)
        agent = await dial(participation, guest)
        guest.socket.account = None
        closing = HeldClose(guest.socket)
        ending = asyncio.create_task(scheduler.advance(SESSION_RECHECK_SECONDS))
        await closing.reached()

        answered = await participation.leave(person_named("guest"), agent.agent_id)

        closing.release.set()
        await ending
        assert answered is AgentRefusal.GONE

    async def test_call_48_a_seat_that_resumes_within_its_window_keeps_its_agent(self, link, participation, scheduler):
        """CALL-48: A seat that resumes inside its window keeps its agent, and nobody is told it left."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)

        await guest.drop()
        await scheduler.advance(RESUME_WINDOW_SECONDS - 1)
        await guest.resume()

        assert agent_frames(host, ParticipationSignal.AGENT_LEFT) == []

    async def test_call_48_the_rooms_end_drops_its_agents_and_sends_nothing(self, link, participation):
        """CALL-48: The room's end drops its agents without an agent_left frame."""
        host, guest = await host_and_guest(link)
        agent = await dial(participation, guest)

        await host.say({"type": SignalType.END_ROOM.value})

        assert agent_frames(guest, ParticipationSignal.AGENT_LEFT) == []
        assert await participation.leave(person_named("guest"), agent.agent_id) is AgentRefusal.GONE


@pytest.mark.asyncio
class TestFramesOnlyTheServerSends:
    @pytest.mark.parametrize("signal", [ParticipationSignal.AGENT_JOINED, ParticipationSignal.AGENT_LEFT])
    async def test_a_browser_that_sends_one_is_refused_as_an_unknown_type(self, link, participation, signal):
        host = await link.join("host")

        await host.say({"type": signal.value, "agent_id": "x"})

        assert [frame["message"] for frame in host.frames(SignalType.ERROR)] == [f"Unknown signal type: {signal.value}"]


@pytest.mark.asyncio
class TestTheAgentsName:
    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="none"),
            pytest.param("   ", id="a blank"),
            pytest.param("x" * (AGENT_NAME_MAX_CHARACTERS + 1), id="over the limit"),
            pytest.param("bad\x00name", id="a control character"),
            pytest.param("bad\nname", id="a line break"),
        ],
    )
    async def test_call_50_a_name_that_is_no_name_is_refused_and_no_tile_shows(self, link, participation, name):
        """CALL-50: A name that is blank, too long or holds a control character is refused with bad_name."""
        host = await link.join("host")

        assert await dial(participation, host, name) is AgentRefusal.BAD_NAME
        assert agent_frames(host, ParticipationSignal.AGENT_JOINED) == []

    @pytest.mark.parametrize(
        ("given", "shown"),
        [
            pytest.param("  Claude  ", "Claude", id="trimmed"),
            pytest.param("x" * AGENT_NAME_MAX_CHARACTERS, "x" * AGENT_NAME_MAX_CHARACTERS, id="at the limit"),
            pytest.param("Host", "Host", id="named like a person"),
        ],
    )
    async def test_call_50_the_agent_shows_under_the_name_its_sponsor_gave_with_the_sponsors_name(
        self, link, participation, given, shown
    ):
        """CALL-50: The agent shows under the name its sponsor gave it, with its sponsor's name beside it."""
        host = await link.join("host")

        await dial(participation, host, given)

        [joined] = agent_frames(host, ParticipationSignal.AGENT_JOINED)
        assert (joined["name"], joined["sponsor"]) == (shown, "host")


@pytest.mark.asyncio
class TestBounds:
    async def test_a_third_agent_in_a_room_is_busy(self, link, participation):
        people = [await link.join("first")]
        room = room_of(people[0])
        people += [await link.join(name, room) for name in ("second", "third")]

        outcomes = [await dial(participation, person) for person in people]

        assert [isinstance(outcome, DialedAgent) for outcome in outcomes] == [True, True, False]
        assert outcomes[2] is AgentRefusal.BUSY
        assert AGENTS_PER_ROOM == 2

    async def test_the_process_cap_is_busy(self, link, participation):
        outcomes = []
        for number in range(AGENTS_PER_PROCESS + 1):
            outcomes.append(await dial(participation, await link.join(f"person{number}")))

        assert outcomes[-1] is AgentRefusal.BUSY
        assert all(isinstance(outcome, DialedAgent) for outcome in outcomes[:-1])


@pytest.mark.asyncio
class TestWhoMayCall:
    async def test_another_persons_agent_id_is_not_yours_and_the_agent_stays(self, link, participation):
        host, guest = await host_and_guest(link)
        agent = await dial(participation, host)

        assert await participation.leave(person_named("guest"), agent.agent_id) is AgentRefusal.NOT_YOURS
        assert agent_frames(guest, ParticipationSignal.AGENT_LEFT) == []

    async def test_an_unknown_id_is_gone(self, link, participation):
        assert await participation.leave(person_named("host"), "no-such-agent") is AgentRefusal.GONE

    async def test_a_leave_by_its_own_person_takes_the_tile_from_every_seat(self, link, participation):
        host, guest = await host_and_guest(link)
        agent = await dial(participation, guest)

        assert await participation.leave(person_named("guest"), agent.agent_id) is None

        assert [len(agent_frames(browser, ParticipationSignal.AGENT_LEFT)) for browser in (host, guest)] == [1, 1]
