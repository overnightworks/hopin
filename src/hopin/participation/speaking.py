"""An agent's spoken sentences: one queue per joined agent, in memory only. A sentence starts when the previous one has
ended, its speaker is answered then, and its sound is kept only while it waits or plays. A sentence's sound grows as its
renderer streams it, so a sentence plays while it is rendered; it ends when its length has run out on the scheduler's
clock, counted from its start, or when its stream fails, which cuts it. A sentence whose voice failed before any sound
takes its turn in the same queue and is written as a chat line instead. A stop cuts the playing sentence and drops every
other one of the agent."""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog
from logline import StepTimer

from hopin.participation.chat_window import utf8_length
from hopin.participation.vocabulary import (
    SENTENCES_WAITING_PER_AGENT,
    AgentRefusal,
    FigureMood,
    SpeakFallback,
    SpeakStarted,
    SpeakStop,
    VoiceFailure,
)
from hopin.rooms.reference import room_reference
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from hopin.participation.participants import DialedAgent
    from hopin.participation.voice import StoredVoice, VoiceStore
    from hopin.signaling.scheduler import Deadlines

MILLISECONDS_PER_SECOND: Final = 1000

logger: structlog.stdlib.BoundLogger = structlog.get_logger()
_STEPS: Final = logging.getLogger(__name__)


def speak_timer(agent: DialedAgent, text: str, *, seq: int | None) -> StepTimer:
    """The timer of the `speak.ended` line of one sentence the agent's speaking asked for, started now; `seq` is the
    newest line of the agent's window when the sentence arrived, which the agent's last pulse told."""
    return StepTimer(
        "speak.ended",
        _STEPS,
        room_ref=room_reference(agent.room_id),
        agent_id=agent.agent_id,
        text_bytes=utf8_length(text),
        seq=seq,
    )


@dataclass(frozen=True)
class SoundLength:
    """The last piece of a sound that came whole: how long it plays."""

    duration_ms: int


type SoundPiece = bytes | SoundLength | VoiceFailure
"""A chunk of the sound, then exactly one last piece: its length when the sound came whole, or why it broke off."""


@dataclass(frozen=True)
class RenderedSentence:
    """A sentence whose sound has begun: its first chunk, and the pieces after it, which end with the sound's length or
    the failure that broke it off. Closing `rest` ends the rendering."""

    first: bytes = field(repr=False)
    rest: AsyncGenerator[SoundPiece] = field(repr=False)


type SpeakResult = SpeakStarted | AgentRefusal | SpeakStop | SpeakFallback
"""That a sentence started to play, that it was written instead, or why it never is said."""

type StopResult = str | AgentRefusal | SpeakStop
"""The text of the line a stop cut, or why nothing was cut."""

type Renderer = Callable[[str, tuple[str, ...]], Awaitable[RenderedSentence | VoiceFailure | None]]
"""Begins the sound of a sentence in the first of the voices its agent kept at accept that the speech service offers
(none kept for the configured voice) and answers with its first chunk; why the voice failed when it makes none; nothing
when the agent may not speak, which no failure of the voice explains."""


type VoicesOffered = Callable[[tuple[str, ...]], Awaitable[bool]]
"""Whether the speech service offers one of the voices an agent kept at accept, the configured one for none, ready
now."""


@dataclass(frozen=True)
class QueuedSentence:
    """A sentence that waits or plays: its text, the mood its agent's figure shows while it plays, its sound's place in
    the store, which grows while `filling` reads the renderer's stream, how long it plays or why its stream broke off
    once that is known (`ended`), and its speaker's answer, which is that it started, or why it never does."""

    text: str
    mood: FigureMood | None
    voice: StoredVoice
    ended: asyncio.Future[int | VoiceFailure] = field(repr=False, compare=False)
    filling: asyncio.Task[None] = field(repr=False, compare=False)
    answer: asyncio.Future[SpeakResult] = field(repr=False, compare=False)


@dataclass(frozen=True)
class WrittenSentence:
    """A sentence whose voice failed: it waits for its turn like a spoken one and is then written as a line."""

    text: str
    answer: asyncio.Future[SpeakResult] = field(repr=False, compare=False)


type WaitingSentence = QueuedSentence | WrittenSentence


class SentenceEnd(StrEnum):
    """How a playing sentence ended: its length ran out and the agent is silent, its length ran out and the agent's
    next sentence starts at once, or an end or a stop cut it."""

    PLAYED = "played"
    CONTINUED = "continued"
    CUT = "cut"


@dataclass
class _Queue:
    """One agent's sentences, kept from its first one until its speaking ends; the queue itself names that span.
    `stops` counts the stops that dropped sentences, which a sentence still being rendered finds when it is done."""

    agent: DialedAgent
    playing: QueuedSentence | None = None
    waiting: deque[WaitingSentence] = field(default_factory=deque)
    rendering: int = 0
    stops: int = 0


class Speaking:
    """The queues of the joined agents' sentences. `start` shows a sentence that begins to play, `ended` tells how
    the playing one ended, before the next one starts, and `write` posts a sentence that takes its turn as a line."""

    def __init__(
        self,
        voices: VoiceStore,
        deadlines: Deadlines,
        start: Callable[[DialedAgent, QueuedSentence], Awaitable[None]],
        ended: Callable[[DialedAgent, QueuedSentence, SentenceEnd], None],
        write: Callable[[DialedAgent, str], Awaitable[None]],
    ) -> None:
        self._voices = voices
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.SENTENCE_END, self._played)
        self._start = start
        self._ended = ended
        self._write = write
        self._queues: dict[str, _Queue] = {}
        self._announcements: dict[str, set[asyncio.Task[None]]] = {}

    async def speak(self, agent: DialedAgent, text: str, render: Renderer, mood: FigureMood | None) -> SpeakResult:
        """Begin the sentence's sound, which plays with `mood`, and keep it as it grows, starting the sentence at once
        when the agent is silent, else after the ones before it. Answers when it starts that it plays, that it was
        written when its voice failed before any sound, or why it never does: no sound came for a reason that is no
        failure of the voice, a stop dropped it while it was rendered or waited, or an end dropped the agent's queue
        meanwhile, which a rejoin or a resume does not undo."""
        queue = self._queues.setdefault(agent.agent_id, _Queue(agent))
        stops = queue.stops
        queue.rendering += 1
        try:
            rendered = await render(text, agent.voices)
        finally:
            queue.rendering -= 1
        if rendered is None:
            return AgentRefusal.UNAVAILABLE
        dropped = self._dropped(agent, queue, stops)
        if dropped is not None:
            if isinstance(rendered, RenderedSentence):
                await rendered.rest.aclose()
            return dropped
        answer: asyncio.Future[SpeakResult] = asyncio.get_running_loop().create_future()
        sentence: WaitingSentence = (
            WrittenSentence(text, answer)
            if isinstance(rendered, VoiceFailure)
            else self._queued(agent, text, mood, rendered, answer)
        )
        if queue.playing is not None or queue.waiting:
            queue.waiting.append(sentence)
            return await _answered_after_waiting(agent, answer)
        if isinstance(sentence, WrittenSentence):
            await self._written(agent, sentence)
        else:
            self._run_in_background(agent, self._begin(agent, queue, sentence))
        return await answer

    def _dropped(self, agent: DialedAgent, queue: _Queue, stops: int) -> AgentRefusal | SpeakStop | None:
        if self._queues.get(agent.agent_id) is not queue:
            return AgentRefusal.GONE
        if queue.stops != stops:
            return SpeakStop.STOPPED
        return None

    def _queued(
        self,
        agent: DialedAgent,
        text: str,
        mood: FigureMood | None,
        rendered: RenderedSentence,
        answer: asyncio.Future[SpeakResult],
    ) -> QueuedSentence:
        voice = self._voices.open(agent.room_id, agent.agent_id)
        voice.append(rendered.first)
        ended: asyncio.Future[int | VoiceFailure] = asyncio.get_running_loop().create_future()
        filling = asyncio.ensure_future(_fill(voice, ended, rendered.rest))
        return QueuedSentence(text, mood, voice, ended, filling, answer)

    def stop(self, agent: DialedAgent) -> str | None:
        """Cut the sentence the agent plays and drop every other sentence of it, rendered or waiting, each answered as
        stopped; the cut sentence's text, or nothing, and nothing dropped, when none plays. The agent speaks again at
        its next sentence."""
        queue = self._queues.get(agent.agent_id)
        cut = None if queue is None else queue.playing
        if queue is None or cut is None:
            return None
        self._deadlines.cancel(DeadlineKind.SENTENCE_END, agent.agent_id)
        queue.playing = None
        queue.stops += 1
        for sentence in queue.waiting:
            if isinstance(sentence, QueuedSentence):
                self._discard(sentence)
            _answer(sentence, SpeakStop.STOPPED)
        queue.waiting.clear()
        self._discard(cut)
        self._ended(agent, cut, SentenceEnd.CUT)
        return cut.text

    def is_full(self, agent: DialedAgent) -> bool:
        """Whether as many sentences already wait behind the agent's playing one or are being rendered for its queue as
        it may keep."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and len(queue.waiting) + queue.rendering >= SENTENCES_WAITING_PER_AGENT

    def is_playing(self, agent: DialedAgent, sentence: QueuedSentence) -> bool:
        """Whether the sentence is the one the agent's queue plays now, no end having dropped it."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and queue.playing is sentence

    def end(self, agent: DialedAgent) -> None:
        """The agent's removal, leave, pause, lease end or call end: drop its queue and its sounds at once."""
        queue = self._queues.pop(agent.agent_id, None)
        if queue is not None:
            self._deadlines.cancel(DeadlineKind.SENTENCE_END, agent.agent_id)
            for sentence in queue.waiting:
                if isinstance(sentence, QueuedSentence):
                    sentence.filling.cancel()
                _answer(sentence, AgentRefusal.GONE)
            if queue.playing is not None:
                queue.playing.filling.cancel()
                self._ended(agent, queue.playing, SentenceEnd.CUT)
        for announcement in self._announcements.pop(agent.agent_id, ()):
            announcement.cancel()
        self._voices.forget_speaker(agent.room_id, agent.agent_id)

    def _run_in_background(self, agent: DialedAgent, announcement: Awaitable[None]) -> None:
        """Run a step of the agent's queue, such as showing a started sentence, without holding its speaker's answer
        back behind a browser's blocked write; the agent's end cancels it."""
        task = asyncio.ensure_future(announcement)
        pending = self._announcements.setdefault(agent.agent_id, set())
        pending.add(task)
        task.add_done_callback(partial(self._announced, agent.agent_id))

    def _announced(self, agent_id: str, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("agent_announcement.failed", agent_id=agent_id)
        pending = self._announcements.get(agent_id)
        if pending is not None:
            pending.discard(task)
            if not pending:
                del self._announcements[agent_id]

    def _begin(self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence) -> Awaitable[None]:
        """Answer the sentence's speaker now and set its end once its sound's length or failure is known; the returned
        announcement shows it."""
        queue.playing = sentence
        _answer(sentence, SpeakStarted.SPOKEN)
        ending = partial(self._set_end, agent, queue, sentence, self._deadlines.now())
        if sentence.ended.done():
            ending(sentence.ended)
        else:
            sentence.ended.add_done_callback(ending)
        return self._start(agent, sentence)

    def _set_end(
        self,
        agent: DialedAgent,
        queue: _Queue,
        sentence: QueuedSentence,
        began_at: float,
        ended: asyncio.Future[int | VoiceFailure],
    ) -> None:
        """A sound that came whole ends when its length has run out since the sentence began; one that broke off ends,
        cut, at once."""
        if self._queues.get(agent.agent_id) is not queue or queue.playing is not sentence:
            return
        outcome = ended.result()
        if isinstance(outcome, VoiceFailure):
            self._run_in_background(agent, self._cut(agent, sentence))
            return
        remaining = began_at + outcome / MILLISECONDS_PER_SECOND - self._deadlines.now()
        self._deadlines.set(DeadlineKind.SENTENCE_END, agent.agent_id, max(remaining, 0))

    async def _played(self, agent_id: str) -> None:
        queue = self._queues[agent_id]
        sentence = queue.playing
        assert sentence is not None  # noqa: S101 - every end of the playing sentence cancels its deadline
        continues = any(isinstance(waiting, QueuedSentence) for waiting in queue.waiting)
        await self._finish(queue.agent, queue, sentence, SentenceEnd.CONTINUED if continues else SentenceEnd.PLAYED)

    async def _cut(self, agent: DialedAgent, sentence: QueuedSentence) -> None:
        queue = self._queues.get(agent.agent_id)
        if queue is None or queue.playing is not sentence:
            return
        await self._finish(agent, queue, sentence, SentenceEnd.CUT)

    async def _finish(self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence, end: SentenceEnd) -> None:
        self._voices.release(sentence.voice.id)
        queue.playing = None
        self._ended(agent, sentence, end)
        await self._advance(agent, queue)

    def _discard(self, sentence: QueuedSentence) -> None:
        """Stop rendering the sentence and drop its sound."""
        sentence.filling.cancel()
        self._voices.release(sentence.voice.id)

    async def _advance(self, agent: DialedAgent, queue: _Queue) -> None:
        """Take the waiting sentences in turn: write each one whose voice failed and start the first that has a sound,
        unless an end or a stop dropped them meanwhile."""
        while queue.waiting and self._queues.get(agent.agent_id) is queue:
            following = queue.waiting.popleft()
            if isinstance(following, QueuedSentence):
                await self._begin(agent, queue, following)
                return
            await self._written(agent, following)

    async def _written(self, agent: DialedAgent, sentence: WrittenSentence) -> None:
        """Post the sentence as a line, then answer its speaker; a write that fails fails the speaker's call instead,
        so the answer never claims a line that did not stand, and the queue goes on."""
        try:
            await self._write(agent, sentence.text)
        except Exception as failure:
            if not sentence.answer.done():
                sentence.answer.set_exception(failure)
            return
        _answer(sentence, SpeakFallback.WRITTEN)


async def _answered_after_waiting(agent: DialedAgent, answer: asyncio.Future[SpeakResult]) -> SpeakResult:
    """The speaker's answer to a sentence that waits behind others, which its caller waits on."""
    with StepTimer(
        "sentence_wait.ended", _STEPS, room_ref=room_reference(agent.room_id), agent_id=agent.agent_id
    ) as step:
        result = await answer
        if isinstance(result, AgentRefusal | SpeakStop):
            step.refuse(result.value)
    return result


async def _fill(
    voice: StoredVoice, ended: asyncio.Future[int | VoiceFailure], pieces: AsyncGenerator[SoundPiece]
) -> None:
    """Keep each chunk of the sound as it arrives, and settle how it ended: whole with its length, or broken off."""
    try:
        async for piece in pieces:
            match piece:
                case bytes():
                    voice.append(piece)
                case SoundLength(duration_ms=duration_ms):
                    voice.finish()
                    ended.set_result(duration_ms)
                case _:
                    voice.break_off()
                    ended.set_result(piece)
                    logger.info("voice_stream.broken", reason=piece.value)
    except Exception:
        logger.error("voice_stream.failed", reason=VoiceFailure.CUT.value)
        voice.break_off()
        ended.set_result(VoiceFailure.CUT)


def _answer(sentence: WaitingSentence, answer: SpeakResult) -> None:
    """Tell the sentence's speaker, unless the speaker gave up waiting."""
    if not sentence.answer.done():
        sentence.answer.set_result(answer)
