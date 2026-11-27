"""A held agent that states it thinks keeps the thinking standing until something ends it,
at participation's real feature and the agents library's real record of who may hear whom."""

import pytest

from hopin.participation.vocabulary import THINKING_MAX_SECONDS, FigureGesture, HoldRefusal, ParticipationSignal
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import SENTENCE_MS, hold_of_a_playing_sentence, speak

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")
THINKING = ParticipationSignal.AGENT_THINKING


@pytest.fixture
def hears():
    return True


def thoughts(browser):
    """Whether the agent thinks, as each seat was told it, in order."""
    return [frame["on"] for frame in browser.frames(THINKING)]


async def a_hold_over_a_thinking_agent(participation, call):
    """The room of a held agent that stated it thinks and whose `speak` without the hold's id was refused `held`."""
    room_id = await hold_of_a_playing_sentence(participation, call)
    await participation.figures.state_turn(GUEST, room_id, thinking=True)
    refused = await participation.speak(GUEST, room_id, "Zwei", None)
    assert refused == HoldRefusal.HELD
    return room_id


class TestAThinkingAgentWhileHeld:
    async def test_call_68_a_speak_refused_held_leaves_the_thinking_standing(self, participation, call):
        """CALL-68."""
        host, guest, *_ = call

        await a_hold_over_a_thinking_agent(participation, call)

        assert [thoughts(seat) for seat in (host, guest)] == [[True]] * 2

    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param(
                lambda participation, room_id, scheduler: participation.figures.state_turn(
                    GUEST, room_id, thinking=False
                ),
                id="the end statement",
            ),
            pytest.param(
                lambda participation, room_id, scheduler: speak(
                    participation, "Zwei", room_id=room_id, interrupted="1"
                ),
                id="a speak naming the hold",
            ),
            pytest.param(
                lambda participation, room_id, scheduler: participation.figures.gesture(
                    GUEST, room_id, FigureGesture.WAVE
                ),
                id="a gesture",
            ),
            pytest.param(
                lambda participation, room_id, scheduler: scheduler.advance(THINKING_MAX_SECONDS), id="the cap"
            ),
        ],
    )
    async def test_call_68_what_ends_the_thinking_of_a_held_agent_tells_every_seat(
        self, participation, call, scheduler, ending
    ):
        """CALL-68."""
        host, guest, *_ = call
        room_id = await a_hold_over_a_thinking_agent(participation, call)

        await ending(participation, room_id, scheduler)

        assert [thoughts(seat) for seat in (host, guest)] == [[True, False]] * 2

    async def test_call_68_an_accepted_speak_after_the_hold_ended_ends_the_thinking(
        self, participation, call, scheduler
    ):
        """CALL-68."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        await participation.figures.state_turn(GUEST, room_id, thinking=True)
        await participation.person_started(room_id, guest.peer_id)
        await participation.speak(GUEST, room_id, "Zwei", None)
        await participation.continue_speaking(GUEST, room_id, "1")
        await scheduler.advance(SENTENCE_MS / 1000 + 1)
        assert thoughts(host) == [True]

        await speak(participation, "Drei", room_id=room_id)

        assert [thoughts(seat) for seat in (host, guest)] == [[True, False]] * 2

    async def test_call_68_the_agents_end_leaves_no_seat_told_the_thinking_ended(self, participation, call, scheduler):
        """CALL-68."""
        host, *_ = call
        room_id = await a_hold_over_a_thinking_agent(participation, call)

        await participation.leave(GUEST, room_id)
        await scheduler.advance(THINKING_MAX_SECONDS)

        assert (thoughts(host), len(host.frames(ParticipationSignal.AGENT_LEFT))) == ([True], 1)
