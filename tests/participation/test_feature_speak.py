"""CALL-60: an agent's sentences at participation's real feature and handler, rendered by a fake renderer: the order
they start in on the scheduler's clock, where each is shown and the ends that drop them."""

import asyncio
import contextlib

import pytest
import pytest_asyncio
import structlog.testing

from hopin.config.constants import SignalType
from hopin.participation.chat_window import LineKind, WindowLine
from hopin.participation.speaking import RenderedSentence
from hopin.participation.vocabulary import (
    LINE_WINDOW_SECONDS,
    LINES_PER_WINDOW,
    AgentRefusal,
    ParticipationSignal,
)
from tests.participation.support.people import person_named, room_of
from tests.signaling.support.browsers import HeldWrite, host_and_guest
from tests.support.speech_origin import failures_in

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HOST = person_named("host")
GUEST = person_named("guest")
VOICE = ParticipationSignal.AGENT_VOICE
LINE = ParticipationSignal.AGENT_LINE
SENTENCE_MS = 1500
FAILED = "agent_speak_failed"
ANNOUNCING_COROUTINE = "_sentence_started"


def rendering(wav=b"sound", duration_ms=SENTENCE_MS):
    async def render(text, voice):
        return RenderedSentence(wav, duration_ms)

    return render


async def speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id):
    """The agent's answer once the sentence started and the announcement of it that follows the answer was sent."""
    before = asyncio.all_tasks()
    answer = await participation.speak(who, room_id, text, rendering() if render is None else render)
    announcements = {task for task in asyncio.all_tasks() - before if task.get_coro().__name__ == ANNOUNCING_COROUTINE}
    if announcements:
        await asyncio.wait(announcements)
    return answer


async def waiting_to_speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id):
    """A `speak` waiting in the agent's queue for its sentence to start; the task holds its answer."""
    answer = asyncio.create_task(participation.speak(who, room_id, text, rendering() if render is None else render))
    await asyncio.sleep(0)
    return answer


async def answers_so_far(scheduler, seconds, *answers):
    """Which of the answers are in after the clock moved on by `seconds`."""
    await scheduler.advance(seconds)
    await asyncio.sleep(0)
    return [answer.done() for answer in answers]


LONG_MS = 60 * 60 * 1000
LONG = rendering(duration_ms=LONG_MS)


async def renders_nothing(sentence, voice):
    return None


async def renders_cancelled(sentence, voice):
    raise asyncio.CancelledError


async def renders_broken(sentence, voice):
    raise RuntimeError("the speech service broke")


def voice_ids(browser):
    return [frame["voice_id"] for frame in browser.frames(VOICE)]


@pytest_asyncio.fixture
async def call(link, participation):
    """A host and a guest in one call, each with an agent of their own dialed in."""
    host, guest = await host_and_guest(link)
    mozart = await participation.dial_in(GUEST, room_of(guest), "Mozart")
    salieri = await participation.dial_in(HOST, room_of(host), "Salieri")
    return host, guest, mozart, salieri


async def a_full_window(participation, room_id, scheduler):
    """The room where the agent plays a sentence with the window's other lines waiting."""
    await speak(participation, room_id=room_id)
    for _ in range(LINES_PER_WINDOW - 1):
        await waiting_to_speak(participation, room_id=room_id)
    return room_id


async def nine_waiting_in(participation, room_id, scheduler):
    """The room where the agent plays an hour-long sentence with nine waiting, its slots free again."""
    await speak(participation, room_id=room_id, render=LONG)
    for _ in range(LINES_PER_WINDOW - 1):
        await waiting_to_speak(participation, room_id=room_id, render=LONG)
    await scheduler.advance(LINE_WINDOW_SECONDS)
    return room_id


async def a_full_queue(participation, room_id, scheduler):
    """The room of `nine_waiting_in`, with one more waiting."""
    await nine_waiting_in(participation, room_id, scheduler)
    await waiting_to_speak(participation, room_id=room_id, render=LONG)
    return room_id


@pytest_asyncio.fixture
async def nine_waiting(participation, call, scheduler):
    """The room where the guest's agent plays an hour-long sentence with nine waiting, its slots free again."""
    host, *_ = call
    return await nine_waiting_in(participation, room_of(host), scheduler)


class TestASentenceStarts:
    async def test_call_60_it_answers_the_length_of_the_sound(self, participation, call):
        host, *_ = call

        answered = await speak(participation, room_id=room_of(host))

        assert answered == SENTENCE_MS

    async def test_call_60_every_seat_is_told_one_voice_of_the_agent_with_the_sound_stored_under_it(
        self, participation, call, voices
    ):
        host, guest, mozart, _ = call

        await speak(participation, room_id=room_of(host), render=rendering(b"the sound"))

        for seat in (host, guest):
            [frame] = seat.frames(VOICE)
            assert (frame["agent_id"], voices.get(frame["voice_id"])) == (mozart.agent_id, b"the sound")

    async def test_call_60_every_seat_reads_it_once_as_the_agents_line(self, participation, call):
        host, guest, mozart, _ = call

        await speak(participation, "Guten Tag", room_id=room_of(host))

        for seat in (host, guest):
            [frame] = seat.frames(LINE)
            assert (frame["agent_id"], frame["name"], frame["text"]) == (mozart.agent_id, "Mozart", "Guten Tag")

    async def test_call_60_it_enters_the_agents_window_as_own_and_spoken_and_every_other_agents_as_spoken(
        self, participation, call
    ):
        host, *_ = call

        await speak(participation, "Guten Tag", room_id=room_of(host))

        assert [participation.read_window(person, room_of(host)).lines for person in (GUEST, HOST)] == [
            (WindowLine(1, LineKind.AGENT, "Mozart", "Guten Tag", own=True, spoken=True),),
            (WindowLine(1, LineKind.AGENT, "Mozart", "Guten Tag", own=False, spoken=True),),
        ]

    async def test_call_60_whitespace_around_the_sentence_is_dropped_and_a_line_break_is_a_space(
        self, participation, call
    ):
        host, *_ = call

        await speak(participation, "  Guten\nTag  ", room_id=room_of(host))

        [frame] = host.frames(LINE)
        assert frame["text"] == "Guten Tag"


class TestTheAnswerComesWhenTheSentenceStarts:
    async def test_call_60_each_waiting_sentence_answers_when_the_previous_ones_length_has_run_out(
        self, participation, call, scheduler
    ):
        host, *_ = call
        room_id = room_of(host)
        first = await speak(participation, room_id=room_id)
        second = await waiting_to_speak(participation, room_id=room_id)
        third = await waiting_to_speak(participation, room_id=room_id)
        seconds = SENTENCE_MS / 1000

        done = [[second.done(), third.done()]]
        for step in (seconds - 0.001, 0.001, seconds - 0.001, 0.001):
            done.append(await answers_so_far(scheduler, step, second, third))

        assert (done, [first, second.result(), third.result()]) == (
            [[False, False], [False, False], [True, False], [True, False], [True, True]],
            [SENTENCE_MS] * 3,
        )

    async def test_call_60_a_sentence_whose_speaker_gave_up_waiting_still_starts_in_order_and_the_queue_goes_on(
        self, participation, call, scheduler
    ):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, "one", room_id=room_id)
        gave_up = await waiting_to_speak(participation, "two", room_id=room_id)
        behind = await waiting_to_speak(participation, "three", room_id=room_id)
        gave_up.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await gave_up

        seconds = SENTENCE_MS / 1000
        await scheduler.advance(seconds)
        await scheduler.advance(seconds)
        await asyncio.sleep(0)
        await scheduler.advance(seconds)
        later = await speak(participation, "four", room_id=room_id)

        assert ([frame["text"] for frame in host.frames(LINE)], behind.result(), later) == (
            ["one", "two", "three", "four"],
            SENTENCE_MS,
            SENTENCE_MS,
        )

    async def test_call_60_an_idle_agents_first_sentence_answers_while_a_browser_write_blocks(
        self, participation, call
    ):
        host, *_ = call
        held = HeldWrite(host.socket, first_only=True)
        speaking = asyncio.create_task(participation.speak(GUEST, room_of(host), "Guten Tag", rendering()))
        await held.reached()
        await asyncio.sleep(0)

        answered_while_blocked = speaking.done()
        held.release.set()
        await speaking

        assert answered_while_blocked

    async def test_call_60_a_refusal_answers_while_the_agents_sentences_still_wait(self, participation, call):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        waiting = await waiting_to_speak(participation, room_id=room_id, render=LONG)

        refusals = [
            await speak(participation, "", room_id=room_id),
            await speak(participation, room_id=room_id, who=person_named("stranger")),
        ]

        assert (refusals, waiting.done()) == ([AgentRefusal.UNAVAILABLE, AgentRefusal.GONE], False)


class TestTheOrder:
    async def test_call_60_three_sentences_start_in_order_each_when_the_previous_ones_length_has_run_out(
        self, participation, call, scheduler, voices
    ):
        host, *_ = call
        sounds = [b"one", b"two", b"three"]
        await speak(participation, "one", room_id=room_of(host), render=rendering(b"one"))
        for sound in sounds[1:]:
            await waiting_to_speak(participation, sound.decode(), room_id=room_of(host), render=rendering(sound))
        started = [len(voice_ids(host))]

        for _ in range(2):
            await scheduler.advance(SENTENCE_MS / 1000 - 0.001)
            started.append(len(voice_ids(host)))
            await scheduler.advance(0.001)
            started.append(len(voice_ids(host)))

        assert started == [1, 1, 2, 2, 3]
        assert [voices.get(voice_id) for voice_id in voice_ids(host)] == [None, None, b"three"]
        assert [frame["text"] for frame in host.frames(LINE)] == ["one", "two", "three"]

    async def test_call_60_a_sentence_waits_for_the_previous_ones_whole_length_even_when_it_is_longer(
        self, participation, call, scheduler
    ):
        host, *_ = call
        await speak(participation, room_id=room_of(host), render=rendering(duration_ms=5000))
        await waiting_to_speak(participation, room_id=room_of(host))

        await scheduler.advance(4.999)
        before = len(voice_ids(host))
        await scheduler.advance(0.001)

        assert (before, len(voice_ids(host))) == (1, 2)

    async def test_call_60_the_sound_of_a_sentence_is_gone_when_its_length_has_run_out(
        self, participation, call, scheduler, voices
    ):
        host, *_ = call
        await speak(participation, room_id=room_of(host))
        [voice_id] = voice_ids(host)

        await scheduler.advance(SENTENCE_MS / 1000)

        assert voices.get(voice_id) is None

    async def test_call_60_each_agent_has_a_queue_of_its_own(self, participation, call):
        host, _, mozart, salieri = call

        await speak(participation, room_id=room_of(host), who=GUEST)
        await speak(participation, room_id=room_of(host), who=HOST)

        assert [frame["agent_id"] for frame in host.frames(VOICE)] == [mozart.agent_id, salieri.agent_id]


async def removed_by_the_host(participation, host, guest, agent):
    await host.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})


async def leaving(participation, host, guest, agent):
    await participation.leave(GUEST, room_of(host))


async def parked_with_its_sponsors_seat(participation, host, guest, agent):
    await guest.drop()


async def left_with_its_sponsors_seat(participation, host, guest, agent):
    await guest.drop(code=1000)


async def ended_with_the_call(participation, host, guest, agent):
    await host.say({"type": SignalType.END_ROOM.value})


class TestEveryEndDropsTheQueue:
    @pytest.mark.parametrize(
        ("end", "next_refusal"),
        [
            pytest.param(removed_by_the_host, AgentRefusal.GONE, id="removal"),
            pytest.param(leaving, AgentRefusal.GONE, id="leave"),
            pytest.param(parked_with_its_sponsors_seat, AgentRefusal.SPONSOR_AWAY, id="pause"),
            pytest.param(left_with_its_sponsors_seat, AgentRefusal.GONE, id="sponsor_leaves"),
            pytest.param(ended_with_the_call, AgentRefusal.GONE, id="call_end"),
        ],
    )
    async def test_call_60_the_waiting_sentences_never_start_the_sound_is_gone_and_the_next_speak_is_refused(
        self, participation, call, scheduler, voices, end, next_refusal
    ):
        host, guest, mozart, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id)
        waiting = await waiting_to_speak(participation, room_id=room_id)
        [playing] = voice_ids(host)

        await end(participation, host, guest, mozart)
        await scheduler.advance(10 * SENTENCE_MS / 1000)
        await scheduler.run_cancelled()  # a timer that had fired before its cancel

        assert (
            voice_ids(host),
            voices.get(playing),
            await waiting,
            await speak(participation, room_id=room_id),
        ) == ([playing], None, AgentRefusal.GONE, next_refusal)


class TestASentenceThatIsNotSpoken:
    async def test_call_60_a_person_with_no_joined_agent_there_is_refused(self, participation, call):
        host, *_ = call

        refused = await speak(participation, room_id=room_of(host), who=person_named("stranger"))

        assert refused is AgentRefusal.GONE

    @pytest.mark.parametrize("text", ["", "   ", "\n\t"])
    async def test_call_60_a_sentence_with_no_text_is_refused_not_rendered_and_logs_no_text(
        self, participation, call, text
    ):
        host, *_ = call
        rendered = []

        async def render(sentence, voice):
            rendered.append(sentence)

        with structlog.testing.capture_logs() as logs:
            refused = await speak(participation, text, render=render, room_id=room_of(host))

        assert (refused, rendered, host.frames(VOICE), failures_in(logs, FAILED)) == (
            AgentRefusal.UNAVAILABLE,
            [],
            [],
            [{"outcome": "no_text"}],
        )

    async def test_call_60_a_renderer_with_no_sound_refuses_unavailable_and_nothing_is_shown(self, participation, call):
        host, *_ = call

        async def render(sentence, voice):
            return None

        refused = await speak(participation, render=render, room_id=room_of(host))

        assert (refused, host.frames(VOICE, LINE)) == (AgentRefusal.UNAVAILABLE, [])

    async def test_call_60_with_no_renderer_it_refuses_unavailable_logs_speaking_off_after_the_place_is_checked(
        self, participation, call
    ):
        host, *_ = call

        with structlog.testing.capture_logs() as logs:
            unavailable = await participation.speak(GUEST, room_of(host), "Hallo", None)
            gone = await participation.speak(person_named("stranger"), room_of(host), "Hallo", None)

        assert (unavailable, gone, failures_in(logs, FAILED)) == (
            AgentRefusal.UNAVAILABLE,
            AgentRefusal.GONE,
            [{"outcome": "speaking_off"}],
        )

    async def test_call_60_an_agent_removed_while_its_sentence_renders_is_refused_and_nothing_is_shown(
        self, participation, call, scheduler
    ):
        host, guest, mozart, _ = call

        async def render_while_removed(sentence, voice):
            await removed_by_the_host(participation, host, guest, mozart)
            return RenderedSentence(b"sound", SENTENCE_MS)

        refused = await speak(participation, render=render_while_removed, room_id=room_of(host))
        await scheduler.advance(10)

        assert (refused, host.frames(VOICE, LINE)) == (AgentRefusal.GONE, [])


class TestTheBounds:
    async def test_call_60_a_sentence_is_spoken_again_once_the_window_has_passed(self, participation, call, scheduler):
        host, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id)
        for _ in range(LINES_PER_WINDOW - 1):
            await waiting_to_speak(participation, room_id=room_id)

        await scheduler.advance(LINE_WINDOW_SECONDS)

        assert await speak(participation, room_id=room_id) == SENTENCE_MS

    async def test_call_60_a_line_the_agent_said_takes_a_slot_the_sentences_share(self, participation, call):
        host, *_ = call
        room_id = room_of(host)
        for _ in range(LINES_PER_WINDOW):
            assert await participation.say(GUEST, room_id, "Hallo") is None

        refused = await speak(participation, room_id=room_id)

        assert refused is AgentRefusal.UNAVAILABLE

    @pytest.mark.parametrize(
        ("arranged", "outcome"), [(a_full_window, "too_fast"), (a_full_queue, "queue_full")], ids=["window", "queue"]
    )
    async def test_call_60_a_sentence_over_a_bound_is_refused_not_rendered_and_logs_its_outcome(
        self, participation, call, scheduler, arranged, outcome
    ):
        room_id = await arranged(participation, room_of(call[0]), scheduler)
        rendered = []

        async def render(sentence, voice):
            rendered.append(sentence)

        with structlog.testing.capture_logs() as logs:
            refused = await speak(participation, render=render, room_id=room_id)

        assert (refused, rendered, failures_in(logs, FAILED)) == (
            AgentRefusal.UNAVAILABLE,
            [],
            [{"outcome": outcome}],
        )

    async def test_call_60_sentences_rendering_at_once_never_leave_more_than_the_queue_allows_waiting(
        self, participation, nine_waiting
    ):
        room_id = nine_waiting

        entered = asyncio.Event()

        async def render_held(sentence, voice):
            entered.set()
            await asyncio.Event().wait()

        with structlog.testing.capture_logs() as logs:
            await waiting_to_speak(participation, render=render_held, room_id=room_id)
            await entered.wait()
            second = await speak(participation, render=LONG, room_id=room_id)

        assert (second, failures_in(logs, FAILED)) == (AgentRefusal.UNAVAILABLE, [{"outcome": "queue_full"}])

    @pytest.mark.parametrize("failing", [renders_nothing, renders_cancelled, renders_broken])
    async def test_call_60_a_sentence_that_fails_to_render_leaves_its_place_in_the_queue_free(
        self, participation, nine_waiting, failing
    ):
        room_id = nine_waiting
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await speak(participation, render=failing, room_id=room_id)
        rendered = []

        async def render_noting(sentence, voice):
            rendered.append(sentence)
            return await LONG(sentence)

        await waiting_to_speak(participation, render=render_noting, room_id=room_id)

        assert rendered == ["Guten Tag"]


class TestAnEndWhileTheSentenceRenders:
    async def test_call_60_an_agent_removed_and_dialed_in_again_meanwhile_is_not_spoken_for(
        self, participation, call, scheduler
    ):
        host, guest, mozart, _ = call

        async def render_while_replaced(sentence, voice):
            await removed_by_the_host(participation, host, guest, mozart)
            await participation.dial_in(GUEST, room_of(host), "Mozart")
            return RenderedSentence(b"sound", SENTENCE_MS)

        refused = await speak(participation, render=render_while_replaced, room_id=room_of(host))
        await scheduler.advance(10)

        assert (refused, host.frames(VOICE, LINE)) == (AgentRefusal.GONE, [])

    async def test_call_60_an_agent_paused_and_resumed_meanwhile_does_not_speak_the_sentence(
        self, participation, call, scheduler
    ):
        host, guest, *_ = call

        async def render_while_paused(sentence, voice):
            await guest.drop()
            await guest.resume()
            return RenderedSentence(b"sound", SENTENCE_MS)

        refused = await speak(participation, render=render_while_paused, room_id=room_of(host))
        await scheduler.advance(10)

        assert (refused, host.frames(VOICE, LINE)) == (AgentRefusal.GONE, [])


class TestAnEndWhileTheSentenceIsAnnounced:
    async def test_call_60_a_removal_while_the_line_is_sent_stops_the_voice_to_every_seat(self, participation, call):
        host, guest, mozart, _ = call
        held = HeldWrite(host.socket, first_only=True)
        speaking = asyncio.create_task(speak(participation, room_id=room_of(host)))
        await held.reached()

        await guest.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": mozart.agent_id})
        held.release.set()
        await speaking

        assert (host.frames(VOICE), guest.frames(VOICE)) == ([], [])

    async def test_call_60_an_agent_removed_before_its_started_sentence_is_announced_shows_and_tells_nothing(
        self, participation, call
    ):
        host, guest, *_ = call
        removals = []

        async def render_then_remove(sentence, voice):
            removals.append(asyncio.ensure_future(participation.leave(GUEST, room_of(host))))
            return RenderedSentence(b"sound", SENTENCE_MS)

        await speak(participation, render=render_then_remove, room_id=room_of(host))

        assert (host.frames(VOICE, LINE), guest.frames(VOICE, LINE)) == ([], [])
