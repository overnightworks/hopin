"""The window of the call's chat a joined agent reads, at participation's real feature and handler."""

import asyncio

import pytest

from hopin.config.constants import SignalType
from hopin.participation.chat_window import LineKind, WindowLine
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.people import person_named, room_of
from tests.signaling.support.browsers import Hold, host_and_guest

pytestmark = pytest.mark.integration

AGENT_NAME = "Claude"
OTHER_AGENT_NAME = "Mozart"
MAX_FRAME_CHARACTERS = 65_536


class HeldFirstWrite(Hold):
    """Holds the first write to one socket before it is sent, until the test releases it; later writes pass."""

    def __init__(self, socket):
        super().__init__()
        send_text = socket.send_text
        held = False

        async def hold_the_first_and_send(text):
            nonlocal held
            if not held:
                held = True
                await self.hold()
            await send_text(text)

        socket.send_text = hold_the_first_and_send


def chat(message):
    return {"type": SignalType.CHAT.value, "message": message}


async def dial(participation, browser, name=AGENT_NAME):
    return await participation.dial_in(person_named(browser.name), room_of(browser), name)


def window_of(participation, browser):
    return participation.read_window(person_named(browser.name), room_of(browser))


def lines_of(participation, browser):
    return window_of(participation, browser).lines


@pytest.mark.asyncio
class TestWhatTheWindowHolds:
    async def test_call_54_a_persons_line_is_held_under_the_name_the_call_shows_and_not_as_the_agents_own(
        self, link, participation
    ):
        """CALL-54: A joined agent's window holds a person's line with kind person and the person's name."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)

        await host.say(chat("hello"))

        assert lines_of(participation, guest) == (WindowLine(1, LineKind.PERSON, "host", "hello", own=False),)

    async def test_call_54_an_agents_line_is_own_in_the_window_of_the_agent_that_said_it_only(
        self, link, participation
    ):
        """CALL-54: An agent's line is held with kind agent and its name, marked own only in its own window."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        await dial(participation, host, OTHER_AGENT_NAME)

        await participation.say(person_named("guest"), room_of(host), "  Hello all  ")

        said = [WindowLine(1, LineKind.AGENT, AGENT_NAME, "Hello all", own=True)]
        assert lines_of(participation, guest) == tuple(said)
        assert lines_of(participation, host) == (WindowLine(1, LineKind.AGENT, AGENT_NAME, "Hello all", own=False),)

    async def test_call_54_a_line_said_before_the_join_is_absent(self, link, participation):
        """CALL-54: A joined agent reads the lines said after its join only."""
        host, guest = await host_and_guest(link)
        await host.say(chat("before"))

        await dial(participation, guest)

        window = window_of(participation, guest)
        assert (window.lines, window.starts_at) == ((), 1)

    async def test_call_54_a_paused_place_goes_on_receiving_lines(self, link, participation):
        """CALL-54: A place whose sponsor seat is parked keeps its window and goes on receiving lines."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        await guest.drop()

        await host.say(chat("while away"))

        assert [line.text for line in lines_of(participation, guest)] == ["while away"]

    async def test_call_54_a_rejoin_is_a_new_window_numbered_from_one(self, link, participation):
        """CALL-54: A new join is a new generation, and its lines count from seq 1."""
        host, guest = await host_and_guest(link)
        first = await dial(participation, guest)
        await host.say(chat("one"))
        await host.say(chat("two"))
        first_window = window_of(participation, guest)
        await participation.leave(person_named("guest"), room_of(host))
        second = await dial(participation, guest)

        await host.say(chat("three"))

        window = window_of(participation, guest)
        assert (first_window.generation, window.generation) == (first.agent_id, second.agent_id)
        assert [(line.seq, line.text) for line in first_window.lines] == [(1, "one"), (2, "two")]
        assert [(line.seq, line.text) for line in window.lines] == [(1, "three")]


@pytest.mark.asyncio
class TestTheBound:
    @pytest.mark.parametrize(
        ("texts", "starts_at", "held"),
        [
            pytest.param(["x"] * 101, 2, 100, id="the_101st_line_evicts_the_first"),
            pytest.param(["a" * MAX_FRAME_CHARACTERS] * 5, 2, 4, id="the_byte_bound_evicts_by_size"),
            pytest.param(["a", "\N{GRINNING FACE}" * MAX_FRAME_CHARACTERS], 2, 1, id="the_newest_line_always_fits"),
        ],
    )
    async def test_call_54_the_oldest_lines_go_first_and_the_start_moves_with_them(
        self, link, participation, texts, starts_at, held
    ):
        """CALL-54: A window holds at most the 100 newest lines and 256 KiB of their text; older lines go first."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)

        for text in texts:
            participation.chat_said(host.peer_id, room_of(host), text)

        window = window_of(participation, guest)
        assert (window.starts_at, len(window.lines)) == (starts_at, held)
        assert window.lines[0].seq == window.starts_at
        assert window.lines[-1].text == texts[-1]


@pytest.mark.asyncio
class TestTheSignal:
    async def test_call_54_a_line_signals_every_agent_in_its_room_the_sayer_included_and_no_other(
        self, link, participation, window_changes
    ):
        """CALL-54: After a line is in a window, the agent of that window is told, the one that said it included."""
        host, guest = await host_and_guest(link)
        elsewhere = await link.join("elsewhere")
        await dial(participation, guest)
        await dial(participation, elsewhere)

        await host.say(chat("hello"))
        await participation.say(person_named("guest"), room_of(host), "an agent speaks")

        assert window_changes == [
            (person_named("guest"), room_of(host)),
            (person_named("guest"), room_of(host)),
        ]


@pytest.mark.asyncio
class TestALineHeldInTheRelay:
    async def test_call_54_an_agent_joining_while_a_persons_line_is_relayed_does_not_get_it(self, link, participation):
        """CALL-54: The audience of a person's line is that of the moment it was said."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        relaying = HeldFirstWrite(guest.socket)

        relayed = asyncio.create_task(host.say(chat("held")))
        await relaying.reached()
        await dial(participation, host, OTHER_AGENT_NAME)
        relaying.release.set()
        await relayed

        assert [len(lines_of(participation, browser)) for browser in (guest, host)] == [1, 0]

    async def test_call_54_an_agent_rejoining_while_an_agents_line_is_sent_does_not_get_it(self, link, participation):
        """CALL-54: The audience of an agent's line is that of the moment it was said."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        await dial(participation, host, OTHER_AGENT_NAME)
        sending = HeldFirstWrite(host.socket)

        sent = asyncio.create_task(participation.say(person_named("guest"), room_of(host), "held"))
        await sending.reached()
        await participation.leave(person_named("host"), room_of(host))
        await dial(participation, host, OTHER_AGENT_NAME)
        sending.release.set()
        await sent

        assert [len(lines_of(participation, browser)) for browser in (guest, host)] == [1, 0]

    async def test_call_54_a_sender_leaving_while_its_line_is_relayed_is_still_read_under_its_name(
        self, link, participation
    ):
        """CALL-54: A line is held under the sender's name as of the moment it was said."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        relaying = HeldFirstWrite(guest.socket)

        relayed = asyncio.create_task(host.say(chat("last words")))
        await relaying.reached()
        leaving = asyncio.create_task(host.drop(1000))
        relaying.release.set()
        await asyncio.gather(relayed, leaving)

        assert [(line.name, line.text) for line in lines_of(participation, guest)] == [("host", "last words")]


async def remove_by_the_host(participation, host, guest):
    await host.say(
        {"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": (window_of(participation, guest)).generation}
    )


async def leave_by_the_agent(participation, host, guest):
    await participation.leave(person_named("guest"), room_of(host))


async def leave_by_the_sponsor(participation, host, guest):
    await guest.drop(1000)


async def end_by_the_host(participation, host, guest):
    await host.say({"type": SignalType.END_ROOM.value})


async def shut_down(participation, host, guest):
    await participation.shut_down()


@pytest.mark.asyncio
class TestTheEnd:
    @pytest.mark.parametrize(
        "end",
        [
            pytest.param(remove_by_the_host, id="removal_by_the_host"),
            pytest.param(leave_by_the_agent, id="leave"),
            pytest.param(leave_by_the_sponsor, id="the_sponsors_seat_leaving"),
            pytest.param(end_by_the_host, id="the_rooms_end"),
            pytest.param(shut_down, id="shutdown"),
        ],
    )
    async def test_call_54_a_window_goes_with_its_place(self, link, participation, end):
        """CALL-54: A window goes with its place: leave, removal, the sponsor leaving, the room's end, shutdown."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        await host.say(chat("hello"))

        await end(participation, host, guest)

        person = person_named("guest")
        assert (participation.window_rooms_of(person), participation.read_window(person, room_of(host))) == ((), None)
