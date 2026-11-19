"""CALL-60: an agent's sentence that plays through the browser's stream while the speech service still renders it."""

import asyncio
from dataclasses import dataclass

import pytest
import pytest_asyncio
import structlog.testing

from hopin.participation.vocabulary import AgentRefusal, SpeakStarted
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import (
    LONG_MS,
    SENTENCE_MS,
    StreamedSentence,
    announced_since,
    answers_so_far,
    renders_failing,
    speak,
    voice_ids,
    waiting_to_speak,
    window_texts,
)
from tests.support.speech_origin import failures_in

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HOST = person_named("host")
GUEST = person_named("guest")


async def first_chunk_of(voices, voice_id):
    return await anext(voices.get(voice_id).read())


@dataclass
class Streaming:
    """The guest's agent playing "Eins", a sentence whose stream the test hands out, in the host's call."""

    room_id: str
    streamed: StreamedSentence
    answer: object


@pytest.fixture
def room_id(call):
    host, *_ = call
    return room_of(host)


@pytest_asyncio.fixture
async def streaming(participation, room_id):
    streamed = StreamedSentence()
    answer = await speak(participation, "Eins", room_id=room_id, render=streamed.render)
    return Streaming(room_id, streamed, answer)


@pytest_asyncio.fixture
async def behind(participation, streaming):
    """The task of the agent's sentence "Zwei", which waits in its queue behind the streamed one."""
    return await waiting_to_speak(participation, "Zwei", room_id=streaming.room_id)


class TestASentenceThatPlaysWhileItRenders:
    async def test_call_60_the_speaker_is_answered_and_every_seat_is_told_the_voice_at_the_first_chunk(
        self, call, streaming, voices
    ):
        host, guest, *_ = call

        assert (streaming.answer, [len(voice_ids(seat)) for seat in (host, guest)]) == (SpeakStarted.SPOKEN, [1, 1])
        assert await first_chunk_of(voices, voice_ids(host)[0]) == b"first"

    async def test_call_60_a_sentence_ends_when_its_length_has_run_out_since_its_first_chunk_whenever_it_was_told(
        self, streaming, behind, scheduler
    ):
        await scheduler.advance(1)
        await streaming.streamed.ends_after(1500)
        done = [await answers_so_far(scheduler, 0.499, behind), await answers_so_far(scheduler, 0.001, behind)]

        assert (done, behind.result()) == ([[False], [True]], SpeakStarted.SPOKEN)

    async def test_call_60_the_next_sentence_is_told_only_after_the_one_that_still_renders_has_ended(
        self, call, streaming, behind, scheduler
    ):
        host, *_ = call

        await scheduler.advance(LONG_MS / 1000)

        assert len(voice_ids(host)) == 1

    async def test_call_61_a_stream_that_breaks_off_after_the_first_chunk_cuts_the_sentence_and_the_queue_goes_on(
        self, participation, call, streaming, behind
    ):
        host, *_ = call

        await streaming.streamed.breaks_off()

        assert (
            await behind,
            [window_texts(participation, person, streaming.room_id) for person in (GUEST, HOST)],
            len(voice_ids(host)),
        ) == (SpeakStarted.SPOKEN, [[("Eins", True, True)], [("Eins", False, True)]], 2)

    async def test_call_61_a_stream_that_crashes_cuts_the_sentence_logs_only_a_closed_code_and_the_queue_goes_on(
        self, participation, streaming, behind
    ):
        with structlog.testing.capture_logs() as logs:
            await streaming.streamed.crashes()
            answer = await behind

        assert (
            answer,
            window_texts(participation, GUEST, streaming.room_id)[0],
            failures_in(logs, "voice_stream.failed"),
        ) == (
            SpeakStarted.SPOKEN,
            ("Eins", True, True),
            [{"reason": "cut"}],
        )
        assert "exc_info" not in [key for log in logs for key in log]

    @pytest.mark.parametrize(
        ("arrive", "behind_the_due_callbacks"),
        [
            pytest.param(
                lambda streamed, then: streamed.ends_after(SENTENCE_MS, then=then), False, id="before_the_end_is_set"
            ),
            pytest.param(lambda streamed, then: streamed.breaks_off(then=then), True, id="before_the_cut_is_shown"),
        ],
    )
    async def test_call_60_a_stop_in_the_turns_after_a_stream_ended_cuts_the_sentence_once_and_sets_no_timer(
        self, participation, streaming, scheduler, arrive, behind_the_due_callbacks
    ):
        known = asyncio.all_tasks()
        timers_before = len(scheduler.pending_timers())
        loop = asyncio.get_running_loop()
        stops = []

        def stop():
            stops.append(asyncio.create_task(participation.stop_speaking(GUEST, streaming.room_id), eager_start=True))

        with structlog.testing.capture_logs() as logs:
            await arrive(streaming.streamed, (lambda: loop.call_soon(stop)) if behind_the_due_callbacks else stop)
            await announced_since(known)

        assert (
            len(stops),
            window_texts(participation, GUEST, streaming.room_id),
            len(scheduler.pending_timers()) - timers_before,
            [log["event"] for log in logs if log["log_level"] == "error"],
        ) == (1, [("Eins", True, True)], 0, [])

    async def test_call_60_an_end_of_the_agent_in_the_turn_after_a_stream_ended_sets_no_timer(
        self, participation, streaming, scheduler, room_id
    ):
        known = asyncio.all_tasks()
        timers_before = len(scheduler.pending_timers())
        leaves = []

        def leave():
            leaves.append(asyncio.create_task(participation.leave(GUEST, room_id), eager_start=True))

        await streaming.streamed.ends_after(SENTENCE_MS, then=leave)
        await asyncio.gather(*leaves)
        await announced_since(known)

        assert (len(leaves), len(scheduler.pending_timers()) - timers_before) == (1, 0)

    async def test_call_60_an_end_of_the_agent_answers_a_written_sentence_waiting_behind_its_sound_as_gone(
        self, participation, room_id
    ):
        await speak(participation, room_id=room_id)
        written = await waiting_to_speak(participation, "Zwei", render=renders_failing, room_id=room_id)

        await participation.leave(GUEST, room_id)

        assert await written == AgentRefusal.GONE

    async def test_call_61_a_sentence_whose_stream_broke_off_while_it_waited_is_cut_at_its_turn(
        self, participation, room_id, scheduler
    ):
        streamed = StreamedSentence()
        await speak(participation, "Eins", room_id=room_id)
        waiting = await waiting_to_speak(participation, "Zwei", room_id=room_id, render=streamed.render)
        await streamed.breaks_off()
        known = asyncio.all_tasks()

        await scheduler.advance(SENTENCE_MS / 1000)
        answer = await waiting
        await announced_since(known)

        assert (answer, window_texts(participation, GUEST, room_id)) == (
            SpeakStarted.SPOKEN,
            [("Eins", True, False), ("Zwei", True, True)],
        )

    async def test_call_60_a_stop_ends_the_rendering_of_the_cut_sentence(self, participation, streaming):
        await participation.stop_speaking(GUEST, streaming.room_id)
        await streaming.streamed.until_closed()

        assert streaming.streamed.closed

    async def test_call_60_an_end_of_the_agent_ends_the_rendering_of_its_sentences(
        self, participation, streaming, room_id
    ):
        waiting = StreamedSentence()
        await waiting_to_speak(participation, room_id=room_id, render=waiting.render)

        await participation.leave(GUEST, room_id)
        await asyncio.gather(streaming.streamed.until_closed(), waiting.until_closed())

        assert (streaming.streamed.closed, waiting.closed) == (True, True)
