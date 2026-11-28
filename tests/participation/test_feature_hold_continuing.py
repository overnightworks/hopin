"""A person the agent hears keeps speaking over a sentence of the agent, and the agent continues or stops at the
hold, at participation's real feature and the agents library's real record of who may hear whom."""

import asyncio

import pytest

from hopin.participation.vocabulary import (
    AgentRefusal,
    HoldRefusal,
    SpeakStarted,
)
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import (
    CONTINUED,
    GUEST,
    HELD,
    LONG,
    SENTENCE_MS,
    VOICE,
    StreamedSentence,
    continues,
    hold_of_a_playing_sentence,
    hold_with_a_sentence_waiting,
    holds,
    rendering,
    sounds,
    speak,
    standing,
    still_rendering,
    voice_ids,
    window_texts,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.hears]


class TestContinuingSpeaking:
    async def test_call_70_a_continue_ends_the_hold_and_the_window_shows_the_sentence_playing_in_one_change(
        self, participation, call, window_changes
    ):
        """CALL-70."""
        room_id = await hold_of_a_playing_sentence(participation, call)
        window_changes.clear()

        continued = await participation.continue_speaking(GUEST, room_id, "1")

        assert (continued, standing(participation, room_id), window_changes) == (
            None,
            (None, True),
            [(GUEST, room_id)],
        )

    async def test_call_70_every_seat_is_told_the_hold_then_the_continue_and_no_stop_in_between(
        self, participation, call
    ):
        """CALL-70."""
        host, guest, mozart, _ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        await participation.continue_speaking(GUEST, room_id, "1")

        assert [(sounds(seat), continues(seat)) for seat in (host, guest)] == [
            ([VOICE.value, HELD.value, CONTINUED.value], [mozart.agent_id])
        ] * 2

    async def test_call_70_the_held_sentence_ends_when_the_time_it_played_has_run_out(
        self, participation, call, scheduler
    ):
        """CALL-70."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        await scheduler.advance(0.5)
        await participation.person_started(room_id, guest.peer_id)
        await scheduler.advance(100)
        await participation.continue_speaking(GUEST, room_id, "1")

        await scheduler.advance(0.9)
        before_its_end = window_texts(participation, GUEST, room_id)
        await scheduler.advance(0.2)

        assert (before_its_end, window_texts(participation, GUEST, room_id)) == ([], [("Eins", True, False)])

    @pytest.mark.parametrize("streamed_at", ["during the hold", "after the continue"])
    async def test_call_70_a_sentence_still_streaming_at_the_hold_ends_when_the_time_it_played_has_run_out(
        self, participation, call, scheduler, streamed_at
    ):
        """CALL-70."""
        host, guest, *_ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await participation.speak(GUEST, room_id, "Eins", streamed.render)
        await scheduler.advance(0.5)
        await participation.person_started(room_id, guest.peer_id)
        if streamed_at == "during the hold":
            await streamed.ends_after(SENTENCE_MS)
        await scheduler.advance(100)
        await participation.continue_speaking(GUEST, room_id, "1")
        if streamed_at == "after the continue":
            await streamed.ends_after(SENTENCE_MS)

        await scheduler.advance(0.9)
        before_its_end = window_texts(participation, GUEST, room_id)
        await scheduler.advance(0.2)

        assert (before_its_end, window_texts(participation, GUEST, room_id)) == ([], [("Eins", True, False)])

    async def test_call_70_the_queue_plays_after_the_continued_sentence(self, participation, call, scheduler):
        """CALL-70."""
        host, *_ = call
        room_id, waiting = await hold_with_a_sentence_waiting(participation, call, "Eins", "Zwei", render=rendering())
        await participation.continue_speaking(GUEST, room_id, "1")
        before_its_end = (waiting.done(), len(voice_ids(host)))

        await scheduler.advance(2 * SENTENCE_MS / 1000)

        assert (before_its_end, await waiting, len(voice_ids(host))) == ((False, 1), SpeakStarted.SPOKEN, 2)

    async def test_call_70_a_hold_over_a_sentence_that_never_started_lets_the_queue_start_at_the_continue(
        self, participation, call
    ):
        """CALL-70."""
        host, guest, *_ = call
        room_id = room_of(host)
        rendered = asyncio.Event()
        rendering_sentence = asyncio.create_task(participation.speak(GUEST, room_id, "Eins", still_rendering(rendered)))
        await asyncio.sleep(0)
        await participation.person_started(room_id, guest.peer_id)
        rendered.set()
        for _ in range(20):
            await asyncio.sleep(0)

        continued = await participation.continue_speaking(GUEST, room_id, "1")

        assert (continued, await rendering_sentence, len(voice_ids(host)), standing(participation, room_id)) == (
            None,
            SpeakStarted.SPOKEN,
            1,
            (None, True),
        )

    async def test_call_70_a_continue_while_the_only_sentence_still_renders_ends_the_hold_and_it_plays_when_rendered(
        self, participation, call
    ):
        """CALL-70."""
        host, guest, *_ = call
        room_id = room_of(host)
        rendered = asyncio.Event()
        rendering_sentence = asyncio.create_task(participation.speak(GUEST, room_id, "Eins", still_rendering(rendered)))
        await asyncio.sleep(0)
        await participation.person_started(room_id, guest.peer_id)

        continued = await participation.continue_speaking(GUEST, room_id, "1")
        rendered.set()

        assert (continued, await rendering_sentence, standing(participation, room_id)) == (
            None,
            SpeakStarted.SPOKEN,
            (None, True),
        )

    @pytest.mark.parametrize("interrupted", ["0", "2", "x"])
    async def test_call_70_a_continue_that_names_another_hold_is_refused_and_changes_nothing(
        self, participation, call, interrupted
    ):
        """CALL-70."""
        host, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        refused = await participation.continue_speaking(GUEST, room_id, interrupted)

        assert (refused, standing(participation, room_id), continues(host)) == (
            HoldRefusal.NOT_INTERRUPTED,
            ("1", False),
            [],
        )

    @pytest.mark.parametrize("sentence_plays", [True, False], ids=["a sentence plays", "the agent never spoke"])
    async def test_call_70_a_continue_while_no_hold_stands_is_refused_not_interrupted(
        self, participation, call, sentence_plays
    ):
        """CALL-70."""
        host, *_ = call
        room_id = room_of(host)
        if sentence_plays:
            await speak(participation, room_id=room_id, render=LONG)

        refused = await participation.continue_speaking(GUEST, room_id, "1")

        assert (refused, continues(host)) == (HoldRefusal.NOT_INTERRUPTED, [])

    async def test_call_70_a_speak_with_the_hold_that_a_continue_ended_meanwhile_is_refused_not_interrupted(
        self, participation, call
    ):
        """CALL-70."""
        host, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        rendered = asyncio.Event()
        speaking_over = asyncio.create_task(
            participation.speak(GUEST, room_id, "Zwei", still_rendering(rendered), interrupted="1")
        )
        await asyncio.sleep(0)
        await participation.continue_speaking(GUEST, room_id, "1")

        rendered.set()

        assert (await speaking_over, sounds(host)) == (
            HoldRefusal.NOT_INTERRUPTED,
            [VOICE.value, HELD.value, CONTINUED.value],
        )

    async def test_call_70_a_continue_of_an_agent_with_no_active_place_is_refused(self, participation, call):
        """CALL-70."""
        host, *_ = call

        refused = await participation.continue_speaking(person_named("stranger"), room_of(host), "1")

        assert refused == AgentRefusal.GONE

    async def test_call_70_a_person_who_is_already_speaking_does_not_hold_the_agent_again(self, participation, call):
        """CALL-70."""
        host, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        await participation.continue_speaking(GUEST, room_id, "1")

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), holds(host)) == ((None, True), [call[2].agent_id])

    async def test_call_70_a_person_who_starts_afresh_holds_the_agent_again_under_a_new_id_and_every_seat_hears_it(
        self, participation, call
    ):
        """CALL-70."""
        host, guest, mozart, _ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        await participation.continue_speaking(GUEST, room_id, "1")
        participation.speech.turn_ended(room_id, guest.peer_id)

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), [holds(seat) for seat in (host, guest)]) == (
            ("2", False),
            [[mozart.agent_id] * 2] * 2,
        )

    async def test_call_70_the_second_hold_is_continued_under_its_own_id_only(self, participation, call):
        """CALL-70."""
        host, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        await participation.continue_speaking(GUEST, room_id, "1")
        participation.speech.turn_ended(room_id, guest.peer_id)
        await participation.person_started(room_id, guest.peer_id)

        stale = await participation.continue_speaking(GUEST, room_id, "1")
        continued = await participation.continue_speaking(GUEST, room_id, "2")

        assert (stale, continued, len(continues(host))) == (HoldRefusal.NOT_INTERRUPTED, None, 2)
