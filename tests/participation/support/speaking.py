"""The agent's side of CALL-60 for the participation tests: a fake renderer and the calls that speak and wait."""

import asyncio

from hopin.participation.speaking import RenderedSentence, SoundLength, _fill
from hopin.participation.vocabulary import ParticipationSignal, VoiceFailure
from tests.participation.support.people import person_named, room_of

GUEST = person_named("guest")
SENTENCE_MS = 1500
LONG_MS = 60 * 60 * 1000


def a_sentence(wav=b"sound", duration_ms=SENTENCE_MS):
    """A sentence whose whole sound is its first chunk and which then ends after `duration_ms`."""

    async def after_the_first_chunk():
        yield SoundLength(duration_ms)

    return RenderedSentence(wav, after_the_first_chunk())


class StreamedSentence:
    """A sentence whose pieces the test hands out: it begins with its first chunk, and `closed` says whether the
    rendering was ended before the stream was."""

    def __init__(self, first=b"first"):
        self._pieces = asyncio.Queue()
        self.closed = False
        self._sentence = RenderedSentence(first, self._stream())

    async def render(self, text, voice):
        return self._sentence

    async def _stream(self):
        try:
            while True:
                piece = await self._pieces.get()
                if isinstance(piece, Exception):
                    raise piece
                yield piece
                if not isinstance(piece, bytes):
                    return
        finally:
            self.closed = True

    async def chunk(self, wav):
        await self._arrive(wav)

    async def ends_after(self, duration_ms):
        await self._arrive(SoundLength(duration_ms))

    async def breaks_off(self, failure=VoiceFailure.CUT):
        await self._arrive(failure)

    async def crashes(self):
        await self._arrive(RuntimeError("the stream crashed"))

    async def _arrive(self, piece):
        self._pieces.put_nowait(piece)
        for _ in range(3):
            await asyncio.sleep(0)


def rendering(wav=b"sound", duration_ms=SENTENCE_MS):
    async def render(text, voice):
        return a_sentence(wav, duration_ms)

    return render


async def speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id, offered=None):
    """The agent's answer once the sentence started and the announcement of it that follows the answer was sent."""
    render = rendering() if render is None else render
    known = asyncio.all_tasks()

    async def render_and_note_the_running_tasks(sentence_text, voice):
        try:
            return await render(sentence_text, voice)
        finally:
            known.update(asyncio.all_tasks())

    answer = await participation.speak(who, room_id, text, render_and_note_the_running_tasks, offered)
    # A stream still being read is no announcement.
    announcements = {
        task
        for task in asyncio.all_tasks() - known - {asyncio.current_task()}
        if task.get_coro().__name__ != _fill.__name__
    }
    if announcements:
        await asyncio.wait(announcements)
    return answer


async def waiting_to_speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id):
    """A `speak` waiting in the agent's queue for its sentence to start; the task holds its answer. The second turn of
    the loop lets the sentence's stream settle its length."""
    answer = asyncio.create_task(participation.speak(who, room_id, text, rendering() if render is None else render))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return answer


async def answers_so_far(scheduler, seconds, *answers):
    """Which of the answers are in after the clock moved on by `seconds`."""
    await scheduler.advance(seconds)
    await asyncio.sleep(0)
    return [answer.done() for answer in answers]


LONG = rendering(duration_ms=LONG_MS)


async def renders_nothing(sentence, voice):
    return None


async def renders_failing(sentence, voice):
    return VoiceFailure.BUSY


async def renders_cancelled(sentence, voice):
    raise asyncio.CancelledError


async def renders_broken(sentence, voice):
    raise RuntimeError("the speech service broke")


async def removed_by_the_host(participation, host, guest, agent):
    await host.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})


async def leaving(participation, host, guest, agent):
    await participation.leave(GUEST, room_of(host))


def voice_ids(browser):
    return [frame["voice_id"] for frame in browser.frames(ParticipationSignal.AGENT_VOICE)]


def window_texts(participation, person, room_id):
    return [(line.text, line.own, line.cut) for line in participation.read_window(person, room_id).lines]
