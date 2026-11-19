"""CALL-69: a person the agent hears who starts speaking while a sentence of the agent plays or waits holds the agent,
at participation's real feature and the agents library's real record of who may hear whom."""

import asyncio

import pytest
import pytest_asyncio

from hopin.participation.vocabulary import (
    AgentRefusal,
    HoldRefusal,
    ParticipationSignal,
    SpeakStarted,
    SpeakStop,
    VoiceFailure,
)
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import (
    LONG,
    SENTENCE_MS,
    StreamedSentence,
    a_sentence,
    speak,
    voice_ids,
    waiting_to_speak,
    window_texts,
)
from tests.signaling.support.browsers import host_and_guest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HOST = person_named("host")
GUEST = person_named("guest")
STOP = ParticipationSignal.AGENT_STOP
VOICE = ParticipationSignal.AGENT_VOICE


@pytest.fixture
def hears():
    return True


@pytest_asyncio.fixture
async def call(link, participation):
    """A host and a guest in one call, each with an agent of their own that hears them."""
    host, guest = await host_and_guest(link)
    mozart = await participation.dial_in(GUEST, room_of(guest), "Mozart")
    await participation.dial_in(HOST, room_of(host), "Salieri")
    return host, guest, mozart


def still_rendering(rendered):
    """A renderer that makes its sentence once the test says so."""

    async def render(text, voice):
        await rendered.wait()
        return a_sentence()

    return render


def stops(browser):
    return [frame["agent_id"] for frame in browser.frames(STOP)]


def standing(participation, room_id, person=GUEST):
    window = participation.read_window(person, room_id)
    record = window.talked_over
    return window.interrupted, None if record is None else record.playing


async def hold_of_a_playing_sentence(participation, call, text="Guten Tag"):
    """The call's room, where the guest's agent plays `text` and the guest has started speaking: its hold."""
    room_id, _ = await hold_with_a_sentence_waiting(participation, call, text, None)
    return room_id


async def hold_with_a_sentence_waiting(participation, call, text="Guten Tag", waiting_text="Zwei"):
    """The room of `hold_of_a_playing_sentence` and the answer of a sentence that waited behind `text` when the hold
    arose."""
    host, guest, _ = call
    room_id = room_of(host)
    await speak(participation, text, room_id=room_id, render=LONG)
    waiting = None if waiting_text is None else await waiting_to_speak(participation, waiting_text, room_id=room_id)
    await participation.person_started(room_id, guest.peer_id)
    return room_id, waiting


class TestAPersonStartsSpeaking:
    async def test_call_69_the_playing_sentence_is_held_and_the_window_shows_the_hold_and_playing_false_in_one_change(
        self, participation, call, window_changes
    ):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        window_changes.clear()

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), window_changes) == (("1", False), [(GUEST, room_id)])

    async def test_call_69_every_seat_is_told_to_stop_the_agents_player_once(self, participation, call):
        host, guest, mozart = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)

        await participation.person_started(room_id, guest.peer_id)

        assert [stops(seat) for seat in (host, guest)] == [[mozart.agent_id]] * 2

    async def test_call_69_the_sentence_that_waits_does_not_start_when_the_played_one_would_have_ended(
        self, participation, call, scheduler
    ):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        waiting = await waiting_to_speak(participation, "Zwei", room_id=room_id)
        await participation.person_started(room_id, guest.peer_id)

        await scheduler.advance(10 * SENTENCE_MS / 1000)

        assert (waiting.done(), len(voice_ids(host)), window_texts(participation, GUEST, room_id)) == (False, 1, [])

    async def test_call_69_a_sentence_still_rendering_counts_as_waiting_and_does_not_start_while_held(
        self, participation, call
    ):
        host, guest, _ = call
        room_id = room_of(host)
        rendered = asyncio.Event()
        rendering_sentence = asyncio.create_task(participation.speak(GUEST, room_id, "Eins", still_rendering(rendered)))
        await asyncio.sleep(0)
        await participation.person_started(room_id, guest.peer_id)

        rendered.set()
        await asyncio.sleep(0.01)

        assert (standing(participation, room_id), rendering_sentence.done(), voice_ids(host), stops(host)) == (
            ("1", False),
            False,
            [],
            [],
        )
        rendering_sentence.cancel()

    async def test_call_69_nothing_is_held_while_the_agent_is_silent(self, participation, call):
        host, guest, _ = call
        room_id = room_of(host)

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), stops(host)) == ((None, None), [])

    async def test_call_69_only_the_agent_that_hears_the_person_is_held(self, participation, call):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await speak(participation, room_id=room_id, render=LONG, who=HOST)

        await participation.person_started(room_id, guest.peer_id)

        assert [standing(participation, room_id, person) for person in (GUEST, HOST)] == [("1", False), (None, None)]

    async def test_call_69_a_person_starting_while_the_agent_is_held_opens_no_second_hold_and_tells_no_second_stop(
        self, participation, call
    ):
        host, guest, mozart = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        participation.speech.turn_ended(room_id, guest.peer_id)

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), stops(host)) == (("1", False), [mozart.agent_id])

    async def test_call_69_nothing_leaves_the_hold_by_itself(self, participation, call, scheduler):
        host, guest, _ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        participation.speech.turn_ended(room_id, guest.peer_id)

        await scheduler.advance(10 * SENTENCE_MS / 1000)

        assert (
            participation.read_window(GUEST, room_id).interrupted,
            window_texts(participation, GUEST, room_id),
            len(voice_ids(host)),
        ) == ("1", [], 1)


class TestASpeakWhileHeld:
    async def test_call_69_a_speak_without_the_hold_is_refused_held_and_changes_nothing(self, participation, call):
        host, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        refused = await speak(participation, "Zwei", room_id=room_id)

        assert (refused, participation.read_window(GUEST, room_id).interrupted, len(voice_ids(host))) == (
            HoldRefusal.HELD,
            "1",
            1,
        )

    @pytest.mark.parametrize("stale", ["0", "2", "x"])
    async def test_call_69_a_speak_with_an_id_that_is_not_the_standing_hold_is_refused_not_interrupted(
        self, participation, call, stale
    ):
        room_id = await hold_of_a_playing_sentence(participation, call)

        refused = await speak(participation, "Zwei", room_id=room_id, interrupted=stale)

        assert (refused, participation.read_window(GUEST, room_id).interrupted) == (HoldRefusal.NOT_INTERRUPTED, "1")

    async def test_call_69_a_speak_with_an_id_while_no_hold_stands_is_refused_not_interrupted(
        self, participation, call
    ):
        host, *_ = call

        refused = await speak(participation, room_id=room_of(host), interrupted="1")

        assert refused == HoldRefusal.NOT_INTERRUPTED

    async def test_call_69_a_speak_with_the_hold_enters_the_cut_line_first_then_plays_at_once(
        self, participation, call
    ):
        host, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call, "Eins")
        voice_before = voice_ids(host)

        answered = await speak(participation, "Zwei", room_id=room_id, interrupted="1")

        assert (
            answered,
            participation.read_window(GUEST, room_id).interrupted,
            window_texts(participation, GUEST, room_id),
        ) == (SpeakStarted.SPOKEN, None, [("Eins", True, True)])
        assert (len(voice_ids(host)), voice_ids(host)[: len(voice_before)]) == (2, voice_before)

    async def test_call_69_a_speak_with_the_hold_answers_the_queued_speaks_stopped(self, participation, call):
        room_id, waiting = await hold_with_a_sentence_waiting(participation, call)

        await speak(participation, "Drei", room_id=room_id, interrupted="1")

        assert await waiting == SpeakStop.STOPPED

    async def test_call_69_a_speak_the_agents_contract_refuses_leaves_the_hold_and_its_rest(self, participation, call):
        room_id = await hold_of_a_playing_sentence(participation, call)

        refused = await participation.speak(GUEST, room_id, "   ", None, interrupted="1")

        assert (refused, participation.read_window(GUEST, room_id).interrupted) == (AgentRefusal.UNAVAILABLE, "1")


class TestAStopWhileHeld:
    @pytest.mark.parametrize("interrupted", ["1", None], ids=["with the standing id", "with none"])
    async def test_call_69_a_stop_ends_the_hold_silent_and_answers_the_text_of_the_cut_sentence(
        self, participation, call, interrupted
    ):
        room_id = await hold_of_a_playing_sentence(participation, call, "Guten Tag")

        answered = await participation.stop_speaking(GUEST, room_id, interrupted)

        assert (answered, participation.read_window(GUEST, room_id).interrupted) == ("Guten Tag", None)
        assert window_texts(participation, GUEST, room_id) == [("Guten Tag", True, True)]

    async def test_call_69_a_stop_of_a_hold_with_no_started_sentence_answers_dropped_and_enters_no_line(
        self, participation, call
    ):
        host, guest, _ = call
        room_id = room_of(host)
        rendered = asyncio.Event()
        rendering_sentence = asyncio.create_task(participation.speak(GUEST, room_id, "Eins", still_rendering(rendered)))
        await asyncio.sleep(0)
        await participation.person_started(room_id, guest.peer_id)

        answered = await participation.stop_speaking(GUEST, room_id, "1")
        rendered.set()

        assert (
            answered,
            participation.read_window(GUEST, room_id).interrupted,
            window_texts(participation, GUEST, room_id),
            await rendering_sentence,
        ) == (SpeakStop.DROPPED, None, [], SpeakStop.STOPPED)

    async def test_call_69_a_stop_with_a_stale_id_is_refused_not_interrupted_and_changes_nothing(
        self, participation, call
    ):
        room_id = await hold_of_a_playing_sentence(participation, call)

        refused = await participation.stop_speaking(GUEST, room_id, "7")

        assert (refused, participation.read_window(GUEST, room_id).interrupted) == (HoldRefusal.NOT_INTERRUPTED, "1")

    @pytest.mark.parametrize("sentence_plays", [True, False], ids=["a sentence plays", "the agent never spoke"])
    async def test_call_69_a_stop_with_an_id_while_no_hold_stands_is_refused_not_interrupted(
        self, participation, call, sentence_plays
    ):
        host, *_ = call
        room_id = room_of(host)
        if sentence_plays:
            await speak(participation, room_id=room_id, render=LONG)

        refused = await participation.stop_speaking(GUEST, room_id, "1")

        assert refused == HoldRefusal.NOT_INTERRUPTED

    async def test_call_69_a_stop_drops_the_queue_behind_the_hold(self, participation, call):
        room_id, waiting = await hold_with_a_sentence_waiting(participation, call)

        await participation.stop_speaking(GUEST, room_id, "1")

        assert await waiting == SpeakStop.STOPPED


class TestTheHoldEnds:
    async def test_call_69_a_voice_failure_while_held_enters_the_started_sentence_cut_and_leaves_the_hold(
        self, participation, call
    ):
        host, guest, _ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await participation.speak(GUEST, room_id, "Eins", streamed.render)
        await participation.person_started(room_id, guest.peer_id)

        await streamed.breaks_off(VoiceFailure.CUT)

        assert (participation.read_window(GUEST, room_id).interrupted, window_texts(participation, GUEST, room_id)) == (
            "1",
            [("Eins", True, True)],
        )

    async def test_call_69_a_held_sentence_whose_sound_runs_out_changes_nothing_and_the_hold_stands(
        self, participation, call, scheduler
    ):
        host, guest, _ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await participation.speak(GUEST, room_id, "Eins", streamed.render)
        await participation.person_started(room_id, guest.peer_id)

        await streamed.ends_after(SENTENCE_MS)
        await scheduler.advance(10 * SENTENCE_MS / 1000)

        assert (participation.read_window(GUEST, room_id).interrupted, window_texts(participation, GUEST, room_id)) == (
            "1",
            [],
        )

    async def test_call_69_a_pause_ends_the_hold_and_the_cut_line_enters(self, participation, call):
        _, guest, _ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        await guest.drop()

        window = participation.read_window(GUEST, room_id)
        assert (window.interrupted, [(line.text, line.cut) for line in window.lines]) == (None, [("Guten Tag", True)])

    async def test_call_69_a_hold_that_arises_while_the_line_is_announced_gives_no_seat_the_voice(
        self, participation, call
    ):
        host, guest, _ = call
        room_id = room_of(host)
        started = asyncio.create_task(speak(participation, room_id=room_id, render=LONG))
        await asyncio.sleep(0)
        await participation.person_started(room_id, guest.peer_id)
        await started

        assert (host.frames(VOICE), standing(participation, room_id)) == ([], ("1", False))
