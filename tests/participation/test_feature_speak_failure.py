"""CALL-61: a sentence of an agent whose voice fails, at participation's real feature and handler: the line it is
written as, the window's voice and the log of an outage."""

import asyncio
import contextlib

import pytest
import structlog.testing

from hopin.participation.chat_window import WindowVoice
from hopin.participation.vocabulary import (
    AgentRefusal,
    ParticipationSignal,
    SpeakFallback,
    SpeakStop,
    VoiceFailure,
)
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import (
    SENTENCE_MS,
    answers_so_far,
    removed_by_the_host,
    rendering,
    renders_broken,
    renders_cancelled,
    renders_failing,
    renders_nothing,
    speak,
    waiting_to_speak,
)
from tests.support.speech_origin import failures_in

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HOST = person_named("host")
GUEST = person_named("guest")
VOICE = ParticipationSignal.AGENT_VOICE
LINE = ParticipationSignal.AGENT_LINE
FAILED = "agent_speak_failed"
OUTAGE = "agent_voice_unavailable"


def written_lines(participation, person, room_id):
    return [(line.text, line.own, line.spoken) for line in participation.read_window(person, room_id).lines]


def voice_of(participation, person, room_id):
    return participation.read_window(person, room_id).voice


class TestAVoiceThatFails:
    async def test_call_61_the_sentence_is_written_as_the_agents_line_and_answered_written(self, participation, call):
        host, guest, mozart, _ = call

        answered = await speak(participation, "Guten Tag", render=renders_failing, room_id=room_of(host))

        assert answered is SpeakFallback.WRITTEN
        for seat in (host, guest):
            [frame] = seat.frames(LINE)
            assert (frame["agent_id"], frame["name"], frame["text"], seat.frames(VOICE)) == (
                mozart.agent_id,
                "Mozart",
                "Guten Tag",
                [],
            )

    async def test_call_61_the_line_enters_every_window_as_unspoken_and_the_agents_own_as_own(
        self, participation, call
    ):
        host, *_ = call
        room_id = room_of(host)

        await speak(participation, "Guten Tag", render=renders_failing, room_id=room_id)

        assert [written_lines(participation, person, room_id) for person in (GUEST, HOST)] == [
            [("Guten Tag", True, False)],
            [("Guten Tag", False, False)],
        ]

    async def test_call_61_it_waits_for_the_playing_sentence_and_enters_after_it(self, participation, call, scheduler):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        written = await waiting_to_speak(participation, "Zwei", render=renders_failing, room_id=room_id)

        answered_early = await answers_so_far(scheduler, SENTENCE_MS / 1000 - 0.001, written)
        before = written_lines(participation, GUEST, room_id)
        await scheduler.advance(0.001)
        await asyncio.sleep(0)

        assert (answered_early, before, await written) == ([False], [], SpeakFallback.WRITTEN)
        assert written_lines(participation, GUEST, room_id) == [("Eins", True, True), ("Zwei", True, False)]

    async def test_call_61_a_sentence_after_a_written_one_still_starts_at_its_turn(
        self, participation, call, scheduler
    ):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        written = await waiting_to_speak(participation, "Zwei", render=renders_failing, room_id=room_id)
        spoken = await waiting_to_speak(participation, "Drei", room_id=room_id)

        await scheduler.advance(SENTENCE_MS / 1000)

        assert (await written, await spoken) == (SpeakFallback.WRITTEN, SENTENCE_MS)

    @pytest.mark.parametrize("speaker_gave_up", [False, True])
    async def test_call_61_a_line_that_cannot_be_written_fails_its_speaker_and_the_queue_goes_on(
        self, participation, call, scheduler, speaker_gave_up
    ):
        host, *_ = call
        room_id = room_of(host)

        send = host.socket.send_text

        async def refuse_the_written_line(frame):
            if "Zwei" in frame:
                raise RuntimeError("closed")
            await send(frame)

        host.socket.send_text = refuse_the_written_line
        await speak(participation, "Eins", room_id=room_id)
        unwritten = await waiting_to_speak(participation, "Zwei", render=renders_failing, room_id=room_id)
        spoken = await waiting_to_speak(participation, "Drei", room_id=room_id)
        if speaker_gave_up:
            unwritten.cancel()

        await scheduler.advance(SENTENCE_MS / 1000)

        with pytest.raises(asyncio.CancelledError if speaker_gave_up else RuntimeError):
            await unwritten
        assert await spoken == SENTENCE_MS

    async def test_call_61_a_stop_drops_a_written_sentence_waiting_and_the_playing_one_stays_a_cut_spoken_line(
        self, participation, call
    ):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        written = await waiting_to_speak(participation, "Zwei", render=renders_failing, room_id=room_id)

        await participation.stop_speaking(GUEST, room_id)

        assert (await written, written_lines(participation, GUEST, room_id)) == (
            SpeakStop.STOPPED,
            [("Eins", True, True)],
        )
        assert [line.cut for line in participation.read_window(GUEST, room_id).lines] == [True]

    async def test_call_61_an_agent_removed_while_the_sentence_renders_writes_nothing(
        self, participation, call, scheduler
    ):
        host, guest, mozart, _ = call

        async def fail_while_removed(sentence, voice):
            await removed_by_the_host(participation, host, guest, mozart)
            return VoiceFailure.BUSY

        refused = await speak(participation, render=fail_while_removed, room_id=room_of(host))

        assert (refused, host.frames(LINE)) == (AgentRefusal.GONE, [])

    async def test_call_61_a_stop_while_the_sentence_renders_writes_nothing(self, participation, call):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)

        async def fail_while_stopped(sentence, voice):
            await participation.stop_speaking(GUEST, room_id)
            return VoiceFailure.BUSY

        stopped = await speak(participation, "Zwei", render=fail_while_stopped, room_id=room_id)

        assert (stopped, [text for text, *_ in written_lines(participation, GUEST, room_id)]) == (
            SpeakStop.STOPPED,
            ["Eins"],
        )

    @pytest.mark.parametrize("unwritten", [renders_nothing, rendering()], ids=["no-consent", "a-sound"])
    async def test_call_61_a_voice_that_has_not_failed_states_nothing(self, participation, call, unwritten):
        host, *_ = call
        room_id = room_of(host)

        await speak(participation, render=unwritten, room_id=room_id)

        assert voice_of(participation, GUEST, room_id) is None


class TestTheVoiceState:
    async def test_call_61_a_failure_turns_it_unavailable_and_the_next_sound_turns_it_available_again(
        self, participation, call, scheduler
    ):
        host, *_ = call
        room_id = room_of(host)
        states = []

        await speak(participation, render=renders_failing, room_id=room_id)
        states.append(voice_of(participation, GUEST, room_id))
        await speak(participation, render=renders_failing, room_id=room_id)
        states.append(voice_of(participation, GUEST, room_id))
        await speak(participation, room_id=room_id)
        states.append(voice_of(participation, GUEST, room_id))

        assert states == [WindowVoice.UNAVAILABLE, WindowVoice.UNAVAILABLE, WindowVoice.AVAILABLE]

    async def test_call_61_only_a_change_of_the_state_tells_the_agent_beyond_the_line_itself(
        self, participation, call, window_changes
    ):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, render=renders_failing, room_id=room_id)
        window_changes.clear()

        await speak(participation, render=renders_failing, room_id=room_id)

        assert window_changes.count((GUEST, room_id)) == 1

    @pytest.mark.parametrize("unrelated", [renders_nothing, renders_cancelled, renders_broken])
    async def test_call_61_a_refusal_that_is_no_failure_of_the_voice_keeps_the_state_it_was_in(
        self, participation, call, unrelated
    ):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, render=renders_failing, room_id=room_id)

        with contextlib.suppress(Exception, asyncio.CancelledError):
            await speak(participation, render=unrelated, room_id=room_id)

        assert voice_of(participation, GUEST, room_id) is WindowVoice.UNAVAILABLE

    async def test_call_61_the_other_agents_window_states_nothing(self, participation, call):
        host, *_ = call
        room_id = room_of(host)

        await speak(participation, render=renders_failing, room_id=room_id)

        assert voice_of(participation, HOST, room_id) is None

    async def test_call_61_the_outage_is_logged_once_with_the_reason_and_the_call_and_logged_again_after_a_sound(
        self, participation, call, scheduler
    ):
        host, *_ = call
        room_id = room_of(host)

        with structlog.testing.capture_logs() as logs:
            await speak(participation, "Geheim", render=renders_failing, room_id=room_id)
            await speak(participation, "Geheim", render=renders_failing, room_id=room_id)
            first = failures_in(logs, OUTAGE)
            await speak(participation, room_id=room_id)
            await scheduler.advance(SENTENCE_MS / 1000)
            await speak(participation, "Geheim", render=renders_failing, room_id=room_id)

        assert (first, failures_in(logs, OUTAGE)) == (
            [{"reason": "busy", "room_id": room_id}],
            [{"reason": "busy", "room_id": room_id}] * 2,
        )
