"""The agent's side of CALL-60 for the participation tests: a fake renderer and the calls that speak and wait."""

import asyncio
from dataclasses import dataclass

from hopin.agent.hearing import MemberHearingRecord
from hopin.participation.hearing import Hearing
from hopin.participation.speaking import RenderedSentence, SoundLength
from hopin.participation.vocabulary import ParticipationSignal, VoiceFailure
from tests.participation.support.people import person_named, room_of
from tests.signaling.support.browsers import SETTLE_TIMEOUT_SECONDS

GUEST = person_named("guest")
HOST = person_named("host")
SENTENCE_MS = 1500
STOP = ParticipationSignal.AGENT_STOP
VOICE = ParticipationSignal.AGENT_VOICE
HELD = ParticipationSignal.AGENT_HELD
CONTINUED = ParticipationSignal.AGENT_CONTINUED
LONG_MS = 60 * 60 * 1000


def a_sentence(wav=b"sound", duration_ms=SENTENCE_MS):
    """A sentence whose whole sound is its first chunk and which then ends after `duration_ms`."""

    async def after_the_first_chunk():
        yield SoundLength(duration_ms)

    return RenderedSentence(wav, after_the_first_chunk())


class StreamedSentence:
    """A sentence whose pieces the test hands out: it begins with its first chunk, and `closed` says whether the
    rendering was ended before the stream was. Each piece it hands out is awaited until the queue has taken it; `then`
    runs in that very step, before anything the taking made due."""

    def __init__(self, first=b"first"):
        self._pieces = asyncio.Queue()
        self._taken = asyncio.Event()
        self._closed = asyncio.Event()
        self._then = None
        self._sentence = RenderedSentence(first, self._stream())

    @property
    def closed(self):
        return self._closed.is_set()

    async def render(self, text, voice):
        return self._sentence

    async def _stream(self):
        try:
            while True:
                piece = await self._pieces.get()
                if isinstance(piece, Exception):
                    raise piece
                yield piece
                # The reader asks for the next piece only when it has kept this one.
                self._taken.set()
                then, self._then = self._then, None
                if then is not None:
                    then()
                if not isinstance(piece, bytes):
                    return
        finally:
            self._closed.set()
            self._taken.set()

    async def until_closed(self):
        async with asyncio.timeout(SETTLE_TIMEOUT_SECONDS):
            await self._closed.wait()

    async def chunk(self, wav):
        await self._arrive(wav)

    async def ends_after(self, duration_ms, *, then=None):
        await self._arrive(SoundLength(duration_ms), then)

    async def breaks_off(self, failure=VoiceFailure.CUT, *, then=None):
        await self._arrive(failure, then)

    async def crashes(self):
        await self._arrive(RuntimeError("the stream crashed"))

    async def _arrive(self, piece, then=None):
        self._taken.clear()
        self._then = then
        self._pieces.put_nowait(piece)
        async with asyncio.timeout(SETTLE_TIMEOUT_SECONDS):
            await self._taken.wait()


def rendering(wav=b"sound", duration_ms=SENTENCE_MS):
    async def render(text, voice):
        return a_sentence(wav, duration_ms)

    return render


class Watch:
    """A render the queue calls, which notes what the queue does with its sentence: `settled` is set once the queue
    reads the sentence's stream or drops it unread, or at once when the render made none; `readers` are the tasks that
    read the streams, `known` the tasks that ran meanwhile, and `finishing` says that the reader was handed the stream's
    last piece, after which `exhausted` is set."""

    def __init__(self, render):
        self._render = render
        self.settled = asyncio.Event()
        self.exhausted = asyncio.Event()
        self.finishing = False
        self.readers = set()
        self._called = False
        self._running = asyncio.all_tasks()

    @property
    def known(self):
        return self._running | self.readers

    async def render(self, text, voice):
        self._called = True
        try:
            rendered = await self._render(text, voice)
        except BaseException:
            self.settled.set()
            raise
        finally:
            self._running.update(asyncio.all_tasks())
        if not isinstance(rendered, RenderedSentence):
            self.settled.set()
            return rendered
        return RenderedSentence(rendered.first, _NotedStream(rendered.rest, self))

    async def until_settled(self, answer=None):
        """Wait until the queue has read the sentence's stream as far as it goes without another piece, or `answer`
        is in; a render the queue never called leaves nothing to wait for."""
        if answer is None and not self._called:
            return
        async with asyncio.timeout(SETTLE_TIMEOUT_SECONDS):
            settling = asyncio.ensure_future(self.settled.wait())
            try:
                await asyncio.wait({settling, answer or settling}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                settling.cancel()
            if self.settled.is_set() and self.finishing:
                await self.exhausted.wait()


class _NotedStream:
    """A sentence's stream, which tells its `Watch` who reads it, when it was dropped unread and when it ran out."""

    def __init__(self, pieces, watch):
        self._pieces = pieces
        self._watch = watch

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._watch.readers.add(asyncio.current_task())
        self._watch.settled.set()
        try:
            piece = await anext(self._pieces)
        except BaseException:
            self._watch.finishing = True
            self._watch.exhausted.set()
            raise
        self._watch.finishing = self._watch.finishing or not isinstance(piece, bytes)
        return piece

    async def aclose(self):
        self._watch.settled.set()
        await self._pieces.aclose()


async def announced_since(known):
    """Wait for the announcements the queue started after the tasks `known` were running."""
    announcements = asyncio.all_tasks() - known - {asyncio.current_task()}
    if announcements:
        await asyncio.wait(announcements)


async def speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id, offered=None, interrupted=None):
    """The agent's answer once the sentence started and the announcement of it that follows the answer was sent;
    `interrupted` names the hold the sentence answers."""
    watch = Watch(rendering() if render is None else render)
    answer = await participation.speak(who, room_id, text, watch.render, offered, interrupted=interrupted)
    await watch.until_settled()
    # A stream still being read is no announcement.
    await announced_since(watch.known)
    return answer


async def waiting_to_speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id):
    """A `speak` waiting in the agent's queue for its sentence to start; the task holds its answer. It returns once the
    queue reads the sentence's stream, so a render that makes none for a while is left to a test that starts the
    speak itself."""
    watch = Watch(rendering() if render is None else render)
    answer = asyncio.create_task(participation.speak(who, room_id, text, watch.render))
    await watch.until_settled(answer)
    return answer


async def answers_so_far(scheduler, seconds, *answers):
    """Which of the answers are in after the clock moved on by `seconds`."""
    await scheduler.advance(seconds)
    done, _ = await asyncio.wait(answers, timeout=0)
    return [answer in done for answer in answers]


LONG = rendering(duration_ms=LONG_MS)


async def renders_nothing(sentence, voice):
    return None


async def renders_failing(sentence, voice):
    return VoiceFailure.BUSY


async def renders_cancelled(sentence, voice):
    raise asyncio.CancelledError


async def renders_broken(sentence, voice):
    raise RuntimeError("the speech service broke")


def sounds(browser, agent=None):
    """The kinds of the sound frames a seat was told, of one agent when named, in the order it was told them."""
    return [
        frame["type"]
        for frame in browser.frames(VOICE, HELD, CONTINUED, STOP)
        if agent is None or frame["agent_id"] == agent.agent_id
    ]


class HearersHostFirst(frozenset):
    """Hearers that iterate with the host first, so the hold of the host's agent comes before the others'."""

    def __iter__(self):
        return iter(sorted(frozenset.__iter__(self), key=lambda person: person != HOST))


class RecordNamingTheHostFirst(MemberHearingRecord):
    def sensing(self, room_id, seat, sense):
        return HearersHostFirst(super().sensing(room_id, seat, sense))


def hearing_naming_the_host_first(member_hearing, *, enabled):
    """The hearing of a seat whose hearers are named with the host's agent first."""
    return Hearing(RecordNamingTheHostFirst(member_hearing), enabled=enabled)


def voice_ids(browser):
    return [frame["voice_id"] for frame in browser.frames(ParticipationSignal.AGENT_VOICE)]


def window_texts(participation, person, room_id):
    return [(line.text, line.own, line.cut) for line in participation.read_window(person, room_id).lines]


async def hold_of_a_playing_sentence(participation, call, text="Guten Tag"):
    """The call's room, where the guest's agent plays `text` and the guest has started speaking: its hold."""
    room_id, _ = await hold_with_a_sentence_waiting(participation, call, text, None)
    return room_id


async def hold_with_a_sentence_waiting(participation, call, text="Guten Tag", waiting_text="Zwei", render=LONG):
    """The room of `hold_of_a_playing_sentence` and the answer of a sentence that waited behind `text` when the hold
    arose."""
    host, guest, *_ = call
    room_id = room_of(host)
    await speak(participation, text, room_id=room_id, render=render)
    waiting = None if waiting_text is None else await waiting_to_speak(participation, waiting_text, room_id=room_id)
    await participation.person_started(room_id, guest.peer_id)
    return room_id, waiting


@dataclass(frozen=True)
class OpenSpeak:
    """A `speak` of the call's agent that has not been answered: its `answer` task, and `release`, which lets its
    render return where the sentence still renders."""

    answer: asyncio.Task
    release: asyncio.Event


async def _open_speak(participation, room_id, *, rendering_it):
    release = asyncio.Event()
    entered = asyncio.Event()
    if not rendering_it:
        return OpenSpeak(await waiting_to_speak(participation, room_id=room_id), release)

    async def gated(text, voice):
        entered.set()
        await release.wait()
        return a_sentence()

    answer = asyncio.create_task(participation.speak(GUEST, room_id, "Guten Tag", gated))
    async with asyncio.timeout(SETTLE_TIMEOUT_SECONDS):
        await entered.wait()
    return OpenSpeak(answer, release)


async def queued_behind_a_playing_sentence(participation, host, guest):
    await speak(participation, room_id=room_of(host), render=LONG)
    return await _open_speak(participation, room_of(host), rendering_it=False)


async def waiting_behind_a_hold(participation, host, guest):
    waiting = await queued_behind_a_playing_sentence(participation, host, guest)
    await participation.person_started(room_of(host), guest.peer_id)
    return waiting


async def rendering_behind_a_playing_sentence(participation, host, guest):
    await speak(participation, room_id=room_of(host), render=LONG)
    return await _open_speak(participation, room_of(host), rendering_it=True)


async def rendering_while_held(participation, host, guest):
    rendering_one = await rendering_behind_a_playing_sentence(participation, host, guest)
    await participation.person_started(room_of(host), guest.peer_id)
    return rendering_one
