"""An end of a call, a seat or an agent while an agent's sentence renders or is announced, at participation's real
feature and handler, rendered by a fake renderer."""

import asyncio

import pytest

from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.support.ends import remove_by_the_host
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import SENTENCE_MS, a_sentence, speak, waiting_to_speak
from tests.signaling.support.browsers import HeldWrite
from tests.support.log_lines import captured_lines

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")
VOICE = ParticipationSignal.AGENT_VOICE
LINE = ParticipationSignal.AGENT_LINE


class TestAnEndWhileTheSentenceRenders:
    async def test_call_60_an_agent_removed_and_dialed_in_again_meanwhile_is_not_spoken_for(
        self, participation, call, scheduler
    ):
        """CALL-60."""
        host, guest, mozart, _ = call

        async def render_while_replaced(sentence, voice):
            await remove_by_the_host(participation, host, guest, mozart)
            await participation.dial_in(GUEST, room_of(host), "Mozart")
            return a_sentence(b"sound", SENTENCE_MS)

        refused = await speak(participation, render=render_while_replaced, room_id=room_of(host))
        await scheduler.advance(10)

        assert (refused, host.frames(VOICE, LINE)) == (AgentRefusal.GONE, [])

    async def test_call_60_an_agent_paused_and_resumed_meanwhile_does_not_speak_the_sentence(
        self, participation, call, scheduler
    ):
        """CALL-60."""
        host, guest, *_ = call

        async def render_while_paused(sentence, voice):
            await guest.drop()
            await guest.resume()
            return a_sentence(b"sound", SENTENCE_MS)

        refused = await speak(participation, render=render_while_paused, room_id=room_of(host))
        await scheduler.advance(10)

        assert (refused, host.frames(VOICE, LINE)) == (AgentRefusal.GONE, [])


async def removed_by_its_sponsor(participation, guest, mozart, room_id):
    await guest.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": mozart.agent_id})


async def stopped_by_its_sponsor(participation, guest, mozart, room_id):
    await participation.stop_speaking(GUEST, room_id)


class TestAnEndWhileTheSentenceIsAnnounced:
    @pytest.mark.parametrize("end", [removed_by_its_sponsor, stopped_by_its_sponsor])
    async def test_call_60_a_removal_or_a_stop_while_the_line_is_sent_gives_no_seat_the_voice(
        self, participation, call, end
    ):
        """CALL-60."""
        host, guest, mozart, _ = call
        held = HeldWrite(host.socket, first_only=True)
        speaking = asyncio.create_task(speak(participation, room_id=room_of(host)))
        await held.reached()

        await end(participation, guest, mozart, room_of(host))
        held.release.set()
        await speaking

        assert (host.frames(VOICE), guest.frames(VOICE)) == ([], [])

    @pytest.mark.parametrize("end", ["room_ended", "leave"])
    async def test_call_60_an_end_in_the_turn_after_the_answer_shows_and_tells_nothing_of_the_sentence(
        self, participation, call, end
    ):
        """CALL-60."""
        host, guest, *_ = call
        room_id = room_of(host)

        async def end_the_agent():
            if end == "room_ended":
                [participation_host] = host.link.handler.features
                participation_host.room_ended(room_id)
            else:
                await participation.leave(GUEST, room_id)

        ending = []

        async def render_then_end_next_turn(sentence, voice):
            ending.append(asyncio.ensure_future(end_the_agent()))
            return a_sentence(b"sound", SENTENCE_MS)

        await participation.speak(GUEST, room_id, "Guten Tag", render_then_end_next_turn)
        await asyncio.gather(*ending)

        assert (host.frames(VOICE, LINE), guest.frames(VOICE, LINE)) == ([], [])

    async def test_call_60_a_sentence_that_finished_while_its_line_was_sent_is_not_given_a_voice(
        self, participation, call, scheduler
    ):
        """CALL-60."""
        host, guest, *_ = call
        held = HeldWrite(host.socket)
        first = asyncio.create_task(speak(participation, "eins", room_id=room_of(host)))
        await held.reached()
        await scheduler.advance(SENTENCE_MS / 1000)
        second = await waiting_to_speak(participation, "zwei", room_id=room_of(host))
        held.release.set()
        await asyncio.gather(first, second)

        assert (sorted(frame["text"] for frame in guest.frames(LINE)), len(guest.frames(VOICE))) == (
            ["eins", "zwei"],
            1,
        )

    async def test_call_60_a_failed_announcement_is_logged_with_no_text_of_the_failure(self, participation, call):
        """CALL-60."""
        host, *_ = call

        async def refuse(text):
            raise RuntimeError("the failure carries the sentence")

        host.socket.send_text = refuse
        with captured_lines() as logs:
            await speak(participation, room_id=room_of(host))

        [failure] = [log for log in logs if log["event"] == "agent_announcement.failed"]
        assert (failure["agent_id"] is not None, "exc_info" in failure, "carries the sentence" in str(failure)) == (
            True,
            False,
            False,
        )
