"""CALL-69: a person the agent hears who starts speaking while a sentence of the agent plays or waits holds the agent,
at participation's real feature and the agents library's real record of who may hear whom."""

import asyncio

import pytest
from agents.member import CONTINUE_SPEAKING_TOOL, STOP_SPEAKING_TOOL

from hopin.agent.tools import CALL_KIND, participation_tools
from hopin.participation.participants import Caller
from hopin.participation.vocabulary import (
    SENTENCES_WAITING_PER_AGENT,
    AgentRefusal,
    HoldRefusal,
    SpeakStarted,
    SpeakStop,
    VoiceFailure,
)
from hopin.signaling.transition import InMemoryCallStore
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import (
    CONTINUED,
    HELD,
    HOST,
    LONG,
    SENTENCE_MS,
    STOP,
    VOICE,
    StreamedSentence,
    a_sentence,
    hold_of_a_playing_sentence,
    hold_with_a_sentence_waiting,
    rendering,
    renders_failing,
    sounds,
    speak,
    voice_ids,
    waiting_to_speak,
    window_texts,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")


@pytest.fixture
def hears():
    return True


def still_rendering(rendered):
    """A renderer that makes its sentence once the test says so."""

    async def render(text, voice):
        await rendered.wait()
        return a_sentence()

    return render


def stops(browser):
    return [frame["agent_id"] for frame in browser.frames(STOP)]


def holds(browser):
    return [frame["agent_id"] for frame in browser.frames(HELD)]


def continues(browser):
    return [frame["agent_id"] for frame in browser.frames(CONTINUED)]


def standing(participation, room_id, person=GUEST):
    window = participation.read_window(person, room_id)
    record = window.talked_over
    return window.interrupted, None if record is None else record.playing


class TestAPersonStartsSpeaking:
    async def test_call_69_a_person_starting_to_speak_where_no_sentence_plays_commits_no_transition(
        self, participation, call, monkeypatch
    ):
        host, guest, *_ = call
        committed = []
        commit = InMemoryCallStore.commit

        async def noting(store, view, change):
            committed.append(change)
            return await commit(store, view, change)

        monkeypatch.setattr(InMemoryCallStore, "commit", noting)

        await participation.person_started(room_of(host), guest.peer_id)

        assert (committed, holds(host)) == ([], [])

    async def test_call_69_the_playing_sentence_is_held_and_the_window_shows_the_hold_and_playing_false_in_one_change(
        self, participation, call, window_changes
    ):
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        window_changes.clear()

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), window_changes) == (("1", False), [(GUEST, room_id)])

    async def test_call_69_every_seat_is_told_the_hold_once_and_no_stop(self, participation, call):
        host, guest, mozart, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)

        await participation.person_started(room_id, guest.peer_id)

        assert [(holds(seat), stops(seat)) for seat in (host, guest)] == [([mozart.agent_id], [])] * 2

    async def test_call_69_every_seat_is_told_the_hold_also_when_no_sentence_had_started(self, participation, call):
        host, guest, mozart, _ = call
        room_id = room_of(host)
        rendered = asyncio.Event()
        rendering_sentence = asyncio.create_task(participation.speak(GUEST, room_id, "Eins", still_rendering(rendered)))
        try:
            await asyncio.sleep(0)

            await participation.person_started(room_id, guest.peer_id)

            assert [holds(seat) for seat in (host, guest)] == [[mozart.agent_id]] * 2
        finally:
            rendering_sentence.cancel()

    async def test_call_69_the_sentence_that_waits_does_not_start_when_the_played_one_would_have_ended(
        self, participation, call, scheduler
    ):
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        waiting = await waiting_to_speak(participation, "Zwei", room_id=room_id)
        await participation.person_started(room_id, guest.peer_id)

        await scheduler.advance(10 * SENTENCE_MS / 1000)

        assert (waiting.done(), len(voice_ids(host)), window_texts(participation, GUEST, room_id)) == (False, 1, [])

    async def test_call_69_a_sentence_still_rendering_counts_as_waiting_and_does_not_start_while_held(
        self, participation, call
    ):
        host, guest, *_ = call
        room_id = room_of(host)
        rendered = asyncio.Event()
        rendering_sentence = asyncio.create_task(participation.speak(GUEST, room_id, "Eins", still_rendering(rendered)))
        try:
            await asyncio.sleep(0)
            await participation.person_started(room_id, guest.peer_id)

            rendered.set()
            for _ in range(20):
                await asyncio.sleep(0)

            assert (standing(participation, room_id), rendering_sentence.done(), voice_ids(host), stops(host)) == (
                ("1", False),
                False,
                [],
                [],
            )
        finally:
            rendering_sentence.cancel()

    async def test_call_69_nothing_is_held_while_the_agent_is_silent(self, participation, call):
        host, guest, *_ = call
        room_id = room_of(host)

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), stops(host)) == ((None, None), [])

    async def test_call_69_only_the_agent_that_hears_the_person_is_held(self, participation, call):
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await speak(participation, room_id=room_id, render=LONG, who=HOST)

        await participation.person_started(room_id, guest.peer_id)

        assert [standing(participation, room_id, person) for person in (GUEST, HOST)] == [("1", False), (None, None)]

    async def test_call_69_a_person_starting_while_the_agent_is_held_opens_no_second_hold_and_tells_no_second_frame(
        self, participation, call
    ):
        host, guest, mozart, _ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        participation.speech.turn_ended(room_id, guest.peer_id)

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), holds(host), stops(host)) == (("1", False), [mozart.agent_id], [])

    async def test_call_69_the_hold_outlives_its_record_and_a_person_starting_afresh_opens_no_new_hold(
        self, participation, call
    ):
        _, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        participation.speech.turn_ended(room_id, guest.peer_id)
        gone = participation.read_window(GUEST, room_id)

        await participation.person_started(room_id, guest.peer_id)

        window = participation.read_window(GUEST, room_id)
        assert (gone.interrupted, gone.talked_over, window.interrupted, window.talked_over.id) == ("1", None, "1", "2")

    async def test_call_69_nothing_leaves_the_hold_by_itself(self, participation, call, scheduler):
        host, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        participation.speech.turn_ended(room_id, guest.peer_id)

        await scheduler.advance(10 * SENTENCE_MS / 1000)

        assert (
            participation.read_window(GUEST, room_id).interrupted,
            window_texts(participation, GUEST, room_id),
            len(voice_ids(host)),
        ) == ("1", [], 1)


class TestASpeakWhileHeld:
    @pytest.mark.parametrize(
        ("interrupted", "refusal"),
        [
            pytest.param(None, HoldRefusal.HELD, id="no id"),
            pytest.param("0", HoldRefusal.NOT_INTERRUPTED, id="an id before the hold"),
            pytest.param("2", HoldRefusal.NOT_INTERRUPTED, id="an id after the hold"),
            pytest.param("x", HoldRefusal.NOT_INTERRUPTED, id="an id that is no number"),
        ],
    )
    async def test_call_69_a_speak_that_names_no_hold_or_another_is_refused_and_changes_nothing(
        self, participation, call, interrupted, refusal
    ):
        host, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        refused = await speak(participation, "Zwei", room_id=room_id, interrupted=interrupted)

        assert (refused, participation.read_window(GUEST, room_id).interrupted, len(voice_ids(host))) == (
            refusal,
            "1",
            1,
        )

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

    async def test_call_69_a_speak_with_the_hold_plays_at_once_although_the_queue_behind_it_is_full(
        self, participation, call
    ):
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        for _ in range(SENTENCES_WAITING_PER_AGENT):
            await waiting_to_speak(participation, room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)

        answered = await speak(participation, "Zwei", room_id=room_id, interrupted="1")

        assert answered == SpeakStarted.SPOKEN

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
        host, guest, *_ = call
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


class TestTheStopToolOverAHoldWithNoStartedSentence:
    async def test_call_69_it_answers_dropped_true_through_the_tool(self, participation, call):
        host, guest, *_ = call
        room_id = room_of(host)
        rendered = asyncio.Event()
        rendering_sentence = asyncio.create_task(participation.speak(GUEST, room_id, "Eins", still_rendering(rendered)))
        try:
            await asyncio.sleep(0)
            await participation.person_started(room_id, guest.peer_id)
            [stop] = [
                tool
                for tool in participation_tools(lambda: participation, None)
                if tool.declaration.name == STOP_SPEAKING_TOOL
            ]

            answered = await stop.handler(Caller(GUEST), {"place": {"kind": CALL_KIND, "id": room_id}})

            assert (answered.is_error, answered.content) == (False, '{"dropped":true}')
        finally:
            rendering_sentence.cancel()


class TestTheContinueTool:
    async def test_call_70_it_refuses_not_active_for_a_place_that_is_no_call(self, participation):
        [continue_tool] = [
            tool
            for tool in participation_tools(lambda: participation, None)
            if tool.declaration.name == CONTINUE_SPEAKING_TOOL
        ]

        answered = await continue_tool.handler(
            Caller(GUEST), {"place": {"kind": "elsewhere", "id": "1"}, "interrupted": "1"}
        )

        assert (answered.is_error, answered.content) == (True, '{"refused":"not_active"}')


class TestTheToolsWithTheSponsorParked:
    @pytest.mark.parametrize(
        ("tool_name", "arguments"),
        [(STOP_SPEAKING_TOOL, {}), (CONTINUE_SPEAKING_TOOL, {"interrupted": "1"})],
    )
    async def test_call_69_70_the_tool_refuses_not_active_while_the_sponsor_is_parked(
        self, participation, call, tool_name, arguments
    ):
        host, guest, *_ = call
        room_id = room_of(host)
        [tool] = [
            tool for tool in participation_tools(lambda: participation, None) if tool.declaration.name == tool_name
        ]
        await guest.drop()

        answered = await tool.handler(Caller(GUEST), {"place": {"kind": CALL_KIND, "id": room_id}, **arguments})

        assert (answered.is_error, answered.content) == (True, '{"refused":"not_active"}')


class TestTheHoldEnds:
    async def test_call_69_a_voice_failure_while_held_enters_the_started_sentence_cut_and_the_hold_stands(
        self, participation, call
    ):
        host, guest, *_ = call
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
        host, guest, *_ = call
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
        _, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        await guest.drop()

        window = participation.read_window(GUEST, room_id)
        assert (window.interrupted, [(line.text, line.cut) for line in window.lines]) == (None, [("Guten Tag", True)])

    async def test_call_69_a_cut_line_is_never_marked_continues(self, participation, call):
        _, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        await guest.drop()

        window = participation.read_window(GUEST, room_id)
        assert [(line.cut, line.continues) for line in window.lines] == [(True, False)]

    async def test_call_69_a_hold_that_arises_while_the_line_is_announced_still_gives_the_seats_the_voice_behind_it(
        self, participation, call
    ):
        host, guest, *_ = call
        room_id = room_of(host)
        started = asyncio.create_task(speak(participation, room_id=room_id, render=LONG))
        await asyncio.sleep(0)
        await participation.person_started(room_id, guest.peer_id)
        await started

        assert (sorted(sounds(host)), standing(participation, room_id)) == (
            sorted([HELD.value, VOICE.value]),
            ("1", False),
        )


class TestContinuingSpeaking:
    async def test_call_70_a_continue_ends_the_hold_and_the_window_shows_the_sentence_playing_in_one_change(
        self, participation, call, window_changes
    ):
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
        host, guest, mozart, _ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        await participation.continue_speaking(GUEST, room_id, "1")

        assert [(sounds(seat), continues(seat)) for seat in (host, guest)] == [
            ([VOICE.value, HELD.value, CONTINUED.value], [mozart.agent_id])
        ] * 2

    async def test_call_70_the_held_sentence_ends_when_the_time_it_played_has_run_out(
        self, participation, call, scheduler
    ):
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
        host, *_ = call
        room_id, waiting = await hold_with_a_sentence_waiting(participation, call, "Eins", "Zwei", render=rendering())
        await participation.continue_speaking(GUEST, room_id, "1")
        before_its_end = (waiting.done(), len(voice_ids(host)))

        await scheduler.advance(2 * SENTENCE_MS / 1000)

        assert (before_its_end, await waiting, len(voice_ids(host))) == ((False, 1), SpeakStarted.SPOKEN, 2)

    async def test_call_70_a_hold_over_a_sentence_that_never_started_lets_the_queue_start_at_the_continue(
        self, participation, call
    ):
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
        host, *_ = call
        room_id = room_of(host)
        if sentence_plays:
            await speak(participation, room_id=room_id, render=LONG)

        refused = await participation.continue_speaking(GUEST, room_id, "1")

        assert (refused, continues(host)) == (HoldRefusal.NOT_INTERRUPTED, [])

    async def test_call_70_a_speak_with_the_hold_that_a_continue_ended_meanwhile_is_refused_not_interrupted(
        self, participation, call
    ):
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
        host, *_ = call

        refused = await participation.continue_speaking(person_named("stranger"), room_of(host), "1")

        assert refused == AgentRefusal.GONE

    async def test_call_70_a_person_who_is_already_speaking_does_not_hold_the_agent_again(self, participation, call):
        host, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        await participation.continue_speaking(GUEST, room_id, "1")

        await participation.person_started(room_id, guest.peer_id)

        assert (standing(participation, room_id), holds(host)) == ((None, True), [call[2].agent_id])

    async def test_call_70_a_person_who_starts_afresh_holds_the_agent_again_under_a_new_id_and_every_seat_hears_it(
        self, participation, call
    ):
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
        host, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)
        await participation.continue_speaking(GUEST, room_id, "1")
        participation.speech.turn_ended(room_id, guest.peer_id)
        await participation.person_started(room_id, guest.peer_id)

        stale = await participation.continue_speaking(GUEST, room_id, "1")
        continued = await participation.continue_speaking(GUEST, room_id, "2")

        assert (stale, continued, len(continues(host))) == (HoldRefusal.NOT_INTERRUPTED, None, 2)


class TestEveryDropAfterAHoldEndsTheSoundBeforeAnyNewVoice:
    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param(lambda participation, room_id: participation.stop_speaking(GUEST, room_id, "1"), id="a stop"),
            pytest.param(
                lambda participation, room_id: participation.stop_speaking(GUEST, room_id), id="a stop with no id"
            ),
        ],
    )
    async def test_call_70_a_stop_ends_the_sound_in_every_seat_and_a_later_speak_sounds_after_it(
        self, participation, call, ending
    ):
        host, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        await ending(participation, room_id)
        await speak(participation, "Zwei", room_id=room_id)

        assert [sounds(seat) for seat in (host, guest)] == [[VOICE.value, HELD.value, STOP.value, VOICE.value]] * 2

    async def test_call_70_a_speak_with_the_hold_ends_the_sound_in_every_seat_before_its_voice(
        self, participation, call
    ):
        host, guest, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        await speak(participation, "Zwei", room_id=room_id, interrupted="1")

        assert [sounds(seat) for seat in (host, guest)] == [[VOICE.value, HELD.value, STOP.value, VOICE.value]] * 2

    async def test_call_70_a_speak_with_the_hold_whose_voice_failed_still_ends_the_sound(self, participation, call):
        host, *_ = call
        room_id = await hold_of_a_playing_sentence(participation, call)

        await speak(participation, "Zwei", room_id=room_id, interrupted="1", render=renders_failing)

        assert sounds(host) == [VOICE.value, HELD.value, STOP.value]

    async def test_call_70_the_sponsors_park_ends_the_sound_in_the_seats_that_remain(self, participation, call):
        host, guest, *_ = call
        await hold_of_a_playing_sentence(participation, call)

        await guest.drop()

        assert sounds(host) == [VOICE.value, HELD.value, STOP.value]

    async def test_call_70_a_park_with_no_hold_tells_no_stop(self, participation, call):
        host, guest, *_ = call
        await speak(participation, room_id=room_of(host), render=LONG)

        await guest.drop()

        assert sounds(host) == [VOICE.value]


class TestASeatThatJoinsDuringAHold:
    async def test_call_70_it_is_told_the_hold_and_hears_the_continue(self, participation, call, link):
        room_id = await hold_of_a_playing_sentence(participation, call)

        late = await link.join("late", room_id)
        await participation.continue_speaking(GUEST, room_id, "1")

        assert sounds(late) == [HELD.value, CONTINUED.value]
