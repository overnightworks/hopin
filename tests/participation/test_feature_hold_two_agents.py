"""A guest's seat heard by two agents holds both and tells every hold before its sound,
at participation's real feature and the agents library's real record of who may hear whom."""

import asyncio

import pytest
import pytest_asyncio

from hopin.agent.hearing import MemberHearingRecord
from hopin.participation.hearing import Hearing
from hopin.participation.vocabulary import Sense, SpeakStarted
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import (
    CONTINUED,
    HELD,
    HOST,
    LONG,
    STOP,
    VOICE,
    announced_since,
    rendering,
    sounds,
    speak,
)
from tests.signaling.support.browsers import HeldWrite

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")


class HearersHostFirst(frozenset):
    """Hearers that iterate with the host first, so the hold of the host's agent comes before the others'."""

    def __iter__(self):
        return iter(sorted(frozenset.__iter__(self), key=lambda person: person != HOST))


class RecordNamingTheHostFirst(MemberHearingRecord):
    def sensing(self, room_id, seat, sense):
        return HearersHostFirst(super().sensing(room_id, seat, sense))


def hearing_naming_the_host_first(member_hearing, *, enabled):
    """The hearing of a seat whose hearers are named with the host's agent first."""
    return Hearing(RecordNamingTheHostFirst(member_hearing), enabled=enabled)


@pytest.fixture
def hears():
    return True


class TestTwoAgentsHearingOneSeat:
    """The guest's seat is heard by two agents; the host seat's first write blocks while the agents are held."""

    @pytest.fixture
    def hearing(self, member_hearing, hears):
        """Names the hearers of a seat with the host's agent first, so the guest's agent is held second."""

        return hearing_naming_the_host_first(member_hearing, enabled=hears)

    @pytest_asyncio.fixture
    async def started_while_the_host_seat_blocks(self, participation, call, member_hearing):
        """The room, the blocked write and the guest's starting to speak, which waits on that write."""
        host, guest, *_ = call
        room_id = room_of(host)
        MemberHearingRecord(member_hearing).allow(room_id, guest.peer_id, HOST, Sense.HEARING, allowed=True)
        await speak(participation, room_id=room_id, render=LONG)
        await speak(participation, room_id=room_id, render=LONG, who=HOST)
        held = HeldWrite(host.socket, first_only=True)
        starting = asyncio.create_task(participation.person_started(room_id, guest.peer_id))
        await held.reached()
        yield room_id, held, starting
        held.release.set()
        await asyncio.gather(starting, return_exceptions=True)

    async def test_call_69_a_speak_with_the_hold_is_accepted_for_both_agents_and_every_hold_is_told_before_its_sound(
        self, participation, call, started_while_the_host_seat_blocks
    ):
        """CALL-69."""
        _, guest, mozart, salieri = call
        room_id, held, starting = started_while_the_host_seat_blocks
        known = asyncio.all_tasks()  # sounds[1:] leaves out the voice of the sentence that played before the hold

        answers = [
            await participation.speak(person, room_id, "Neu", rendering(), interrupted="1") for person in (HOST, GUEST)
        ]
        held.release.set()
        await starting
        await announced_since(known)

        assert (answers, [sounds(guest, agent)[1:] for agent in (mozart, salieri)]) == (
            [SpeakStarted.SPOKEN] * 2,
            [[HELD.value, STOP.value, VOICE.value]] * 2,
        )

    async def test_call_69_a_continue_with_the_hold_is_accepted_for_both_agents_and_told_after_the_hold(
        self, participation, call, started_while_the_host_seat_blocks
    ):
        """CALL-69."""
        _, guest, mozart, salieri = call
        room_id, held, starting = started_while_the_host_seat_blocks

        continuing = [
            asyncio.create_task(participation.continue_speaking(person, room_id, "1")) for person in (HOST, GUEST)
        ]
        await asyncio.sleep(0)
        held.release.set()
        await starting

        assert (await asyncio.gather(*continuing), [sounds(guest, agent)[1:] for agent in (mozart, salieri)]) == (
            [None, None],
            [[HELD.value, CONTINUED.value]] * 2,
        )

    async def test_call_69_an_agent_paused_while_another_hold_is_being_told_is_not_held_afterwards(
        self, participation, call, started_while_the_host_seat_blocks
    ):
        """CALL-69."""
        _, guest, *_ = call
        room_id, held, starting = started_while_the_host_seat_blocks

        await guest.drop()
        held.release.set()
        started = await asyncio.gather(starting, return_exceptions=True)

        assert (participation.read_window(GUEST, room_id).interrupted, started) == (None, [None])
