"""CALL-60: an agent's sentences at participation's real feature and handler, rendered by a fake renderer: the order
they start in on the scheduler's clock, where each is shown and the ends that drop them."""

import pytest
import pytest_asyncio

from hopin.config.constants import SignalType
from hopin.participation.chat_window import LineKind, WindowLine
from hopin.participation.speaking import RenderedSentence
from hopin.participation.vocabulary import AgentRefusal, ParticipationSignal
from tests.participation.support.people import person_named, room_of
from tests.signaling.support.browsers import host_and_guest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HOST = person_named("host")
GUEST = person_named("guest")
VOICE = ParticipationSignal.AGENT_VOICE
LINE = ParticipationSignal.AGENT_LINE
SENTENCE_MS = 1500


def rendering(wav=b"sound", duration_ms=SENTENCE_MS):
    async def render(text):
        return RenderedSentence(wav, duration_ms)

    return render


async def speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id):
    return await participation.speak(who, room_id, text, rendering() if render is None else render)


def voice_ids(browser):
    return [frame["voice_id"] for frame in browser.frames(VOICE)]


@pytest_asyncio.fixture
async def call(link, participation):
    """A host and a guest in one call, each with an agent of their own dialed in."""
    host, guest = await host_and_guest(link)
    mozart = await participation.dial_in(GUEST, room_of(guest), "Mozart")
    salieri = await participation.dial_in(HOST, room_of(host), "Salieri")
    return host, guest, mozart, salieri


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


class TestTheOrder:
    async def test_call_60_three_sentences_start_in_order_each_when_the_previous_ones_length_has_run_out(
        self, participation, call, scheduler, voices
    ):
        host, *_ = call
        sounds = [b"one", b"two", b"three"]
        for sound in sounds:
            await speak(participation, sound.decode(), room_id=room_of(host), render=rendering(sound))
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
        await speak(participation, room_id=room_of(host))

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
        await speak(participation, room_id=room_id)
        [playing] = voice_ids(host)

        await end(participation, host, guest, mozart)
        await scheduler.advance(10 * SENTENCE_MS / 1000)
        await scheduler.run_cancelled()  # a timer that had fired before its cancel

        assert (
            voice_ids(host),
            voices.get(playing),
            await speak(participation, room_id=room_id),
        ) == ([playing], None, next_refusal)


class TestASentenceThatIsNotSpoken:
    async def test_call_60_a_person_with_no_joined_agent_there_is_refused(self, participation, call):
        host, *_ = call

        refused = await speak(participation, room_id=room_of(host), who=person_named("stranger"))

        assert refused is AgentRefusal.GONE

    @pytest.mark.parametrize("text", ["", "   ", "\n\t"])
    async def test_call_60_a_sentence_with_no_text_is_refused_and_not_rendered(self, participation, call, text):
        host, *_ = call
        rendered = []

        async def render(sentence):
            rendered.append(sentence)

        refused = await speak(participation, text, render=render, room_id=room_of(host))

        assert (refused, rendered, host.frames(VOICE)) == (AgentRefusal.UNAVAILABLE, [], [])

    async def test_call_60_a_renderer_with_no_sound_refuses_unavailable_and_nothing_is_shown(self, participation, call):
        host, *_ = call

        async def render(sentence):
            return None

        refused = await speak(participation, render=render, room_id=room_of(host))

        assert (refused, host.frames(VOICE, LINE)) == (AgentRefusal.UNAVAILABLE, [])

    async def test_call_60_with_no_renderer_it_refuses_unavailable_after_the_place_is_checked(
        self, participation, call
    ):
        host, *_ = call

        unavailable = await participation.speak(GUEST, room_of(host), "Hallo", None)
        gone = await participation.speak(person_named("stranger"), room_of(host), "Hallo", None)

        assert (unavailable, gone) == (AgentRefusal.UNAVAILABLE, AgentRefusal.GONE)

    async def test_call_60_an_agent_removed_while_its_sentence_renders_is_refused_and_nothing_is_shown(
        self, participation, call, scheduler
    ):
        host, guest, mozart, _ = call

        async def render_while_removed(sentence):
            await removed_by_the_host(participation, host, guest, mozart)
            return RenderedSentence(b"sound", SENTENCE_MS)

        refused = await speak(participation, render=render_while_removed, room_id=room_of(host))
        await scheduler.advance(10)

        assert (refused, host.frames(VOICE, LINE)) == (AgentRefusal.GONE, [])
