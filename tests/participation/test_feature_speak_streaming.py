"""CALL-60: an agent's sentence that plays through the browser's stream while the speech service still renders it."""

import asyncio

import pytest

from hopin.participation.vocabulary import SpeakStarted
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import (
    LONG_MS,
    SENTENCE_MS,
    StreamedSentence,
    answers_so_far,
    leaving,
    speak,
    voice_ids,
    waiting_to_speak,
    window_texts,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HOST = person_named("host")
GUEST = person_named("guest")


async def first_chunk_of(voices, voice_id):
    return await anext(voices.get(voice_id).read())


class TestASentenceThatPlaysWhileItRenders:
    async def test_call_60_the_speaker_is_answered_and_every_seat_is_told_the_voice_at_the_first_chunk(
        self, participation, call, voices
    ):
        host, guest, *_ = call
        streamed = StreamedSentence(b"first")

        answer = await speak(participation, room_id=room_of(host), render=streamed.render)

        assert (answer, [len(voice_ids(seat)) for seat in (host, guest)]) == (SpeakStarted.SPOKEN, [1, 1])
        assert await first_chunk_of(voices, voice_ids(host)[0]) == b"first"

    async def test_call_60_a_sentence_ends_when_its_length_has_run_out_since_its_first_chunk_whenever_it_was_told(
        self, participation, call, scheduler
    ):
        host, *_ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await speak(participation, room_id=room_id, render=streamed.render)
        behind = await waiting_to_speak(participation, room_id=room_id)

        await scheduler.advance(1)
        await streamed.ends_after(1500)
        done = [await answers_so_far(scheduler, 0.499, behind), await answers_so_far(scheduler, 0.001, behind)]

        assert (done, behind.result()) == ([[False], [True]], SpeakStarted.SPOKEN)

    async def test_call_60_the_next_sentence_is_told_only_after_the_one_that_still_renders_has_ended(
        self, participation, call, scheduler
    ):
        host, *_ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await speak(participation, room_id=room_id, render=streamed.render)
        await waiting_to_speak(participation, room_id=room_id)

        await scheduler.advance(LONG_MS / 1000)

        assert len(voice_ids(host)) == 1

    async def test_call_61_a_stream_that_breaks_off_after_the_first_chunk_cuts_the_sentence_and_the_queue_goes_on(
        self, participation, call
    ):
        host, *_ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await speak(participation, "Eins", room_id=room_id, render=streamed.render)
        behind = await waiting_to_speak(participation, "Zwei", room_id=room_id)

        await streamed.breaks_off()

        assert (
            await behind,
            [window_texts(participation, person, room_id) for person in (GUEST, HOST)],
            len(voice_ids(host)),
        ) == (SpeakStarted.SPOKEN, [[("Eins", True, True)], [("Eins", False, True)]], 2)

    async def test_call_61_a_stream_that_crashes_cuts_the_sentence_and_the_queue_goes_on(self, participation, call):
        host, *_ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await speak(participation, "Eins", room_id=room_id, render=streamed.render)
        behind = await waiting_to_speak(participation, "Zwei", room_id=room_id)

        await streamed.crashes()

        assert (await behind, window_texts(participation, GUEST, room_id)[0]) == (
            SpeakStarted.SPOKEN,
            ("Eins", True, True),
        )

    async def test_call_61_a_sentence_whose_stream_broke_off_while_it_waited_is_cut_at_its_turn(
        self, participation, call, scheduler
    ):
        host, *_ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await speak(participation, "Eins", room_id=room_id)
        waiting = asyncio.create_task(participation.speak(GUEST, room_id, "Zwei", streamed.render))
        await asyncio.sleep(0)
        await streamed.breaks_off()

        await scheduler.advance(SENTENCE_MS / 1000)

        assert (await waiting, window_texts(participation, GUEST, room_id)) == (
            SpeakStarted.SPOKEN,
            [("Eins", True, False), ("Zwei", True, True)],
        )

    async def test_call_60_a_stop_ends_the_rendering_of_the_cut_sentence(self, participation, call):
        host, *_ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await speak(participation, room_id=room_id, render=streamed.render)

        await participation.stop_speaking(GUEST, room_id)
        await asyncio.sleep(0)

        assert streamed.closed

    async def test_call_60_an_end_of_the_agent_ends_the_rendering_of_its_sentences(self, participation, call):
        host, guest, mozart, _ = call
        room_id = room_of(host)
        playing, waiting = StreamedSentence(), StreamedSentence()
        await speak(participation, room_id=room_id, render=playing.render)
        await waiting_to_speak(participation, room_id=room_id, render=waiting.render)

        await leaving(participation, host, guest, mozart)
        await asyncio.sleep(0)

        assert (playing.closed, waiting.closed) == (True, True)
