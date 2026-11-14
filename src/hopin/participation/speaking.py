"""An agent's spoken sentences: one queue per joined agent, in memory only. A sentence starts when the previous one's
length has run out on the scheduler's clock, its speaker is answered then, and its sound is kept only while it waits or
plays. A sentence whose voice failed takes its turn in the same queue and is written as a chat line instead. A stop cuts
the playing sentence and drops every other one of the agent."""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog

from hopin.participation.vocabulary import (
    SENTENCES_WAITING_PER_AGENT,
    AgentRefusal,
    SpeakFallback,
    SpeakStop,
    VoiceFailure,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hopin.participation.participants import DialedAgent
    from hopin.participation.voice import StoredVoice, VoiceStore
    from hopin.signaling.scheduler import Scheduler, TimerHandle

MILLISECONDS_PER_SECOND: Final = 1000

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class RenderedSentence:
    """A sentence's sound, as the renderer made it, and how long it plays."""

    wav: bytes = field(repr=False)
    duration_ms: int


type SpeakResult = int | AgentRefusal | SpeakStop | SpeakFallback
"""How long a sentence plays once it starts, that it was written instead, or why it never is said."""

type StopResult = str | AgentRefusal | SpeakStop
"""The text of the line a stop cut, or why nothing was cut."""

type Renderer = Callable[[str, str | None], Awaitable[RenderedSentence | VoiceFailure | None]]
"""Makes the sound of a sentence in the voice its agent kept at accept (none for the configured one); why the voice
failed when it cannot; nothing when the agent may not speak, which no failure of the voice explains."""


@dataclass(frozen=True)
class QueuedSentence:
    """A sentence that waits or plays: its text, its sound's place in the store, how long it plays and its speaker's
    answer, which is how long it plays once it starts, or why it never does."""

    text: str
    voice: StoredVoice
    duration_ms: int
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

    playing: QueuedSentence | None = None
    waiting: deque[WaitingSentence] = field(default_factory=deque)
    rendering: int = 0
    timer: TimerHandle | None = None
    stops: int = 0


class Speaking:
    """The queues of the joined agents' sentences. `start` shows a sentence that begins to play, `ended` tells how
    the playing one ended, before the next one starts, and `write` posts a sentence that takes its turn as a line."""

    def __init__(
        self,
        voices: VoiceStore,
        scheduler: Scheduler,
        start: Callable[[DialedAgent, QueuedSentence], Awaitable[None]],
        ended: Callable[[DialedAgent, QueuedSentence, SentenceEnd], None],
        write: Callable[[DialedAgent, str], Awaitable[None]],
    ) -> None:
        self._voices = voices
        self._scheduler = scheduler
        self._start = start
        self._ended = ended
        self._write = write
        self._queues: dict[str, _Queue] = {}
        self._announcements: dict[str, set[asyncio.Task[None]]] = {}

    async def speak(self, agent: DialedAgent, text: str, render: Renderer) -> SpeakResult:
        """Render the sentence and keep its sound, starting it at once when the agent is silent, else after the ones
        before it. Answers when it starts how long it plays, that it was written when its voice failed, or why it never
        does: no sound came for a reason that is no failure of the voice, a stop dropped it while it was rendered or
        waited, or an end dropped the agent's queue meanwhile, which a rejoin or a resume does not undo."""
        queue = self._queues.setdefault(agent.agent_id, _Queue())
        stops = queue.stops
        queue.rendering += 1
        try:
            rendered = await render(text, agent.voice)
        finally:
            queue.rendering -= 1
        if rendered is None:
            return AgentRefusal.UNAVAILABLE
        if self._queues.get(agent.agent_id) is not queue:
            return AgentRefusal.GONE
        if queue.stops != stops:
            return SpeakStop.STOPPED
        answer: asyncio.Future[SpeakResult] = asyncio.get_running_loop().create_future()
        sentence: WaitingSentence = (
            WrittenSentence(text, answer)
            if isinstance(rendered, VoiceFailure)
            else QueuedSentence(
                text, self._voices.put(agent.room_id, agent.agent_id, rendered.wav), rendered.duration_ms, answer
            )
        )
        if queue.playing is not None or queue.waiting:
            queue.waiting.append(sentence)
        elif isinstance(sentence, WrittenSentence):
            await self._written(agent, sentence)
        else:
            self._announce_in_background(agent, self._begin(agent, queue, sentence))
        return await answer

    def stop(self, agent: DialedAgent) -> str | None:
        """Cut the sentence the agent plays and drop every other sentence of it, rendered or waiting, each answered as
        stopped; the cut sentence's text, or nothing, and nothing dropped, when none plays. The agent speaks again at
        its next sentence."""
        queue = self._queues.get(agent.agent_id)
        cut = None if queue is None else queue.playing
        if queue is None or cut is None:
            return None
        _cancel_timer(queue)
        queue.playing = None
        queue.stops += 1
        for sentence in queue.waiting:
            if isinstance(sentence, QueuedSentence):
                self._voices.release(sentence.voice.id)
            _answer(sentence, SpeakStop.STOPPED)
        queue.waiting.clear()
        self._voices.release(cut.voice.id)
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
            _cancel_timer(queue)
            for sentence in queue.waiting:
                _answer(sentence, AgentRefusal.GONE)
            if queue.playing is not None:
                self._ended(agent, queue.playing, SentenceEnd.CUT)
        for announcement in self._announcements.pop(agent.agent_id, ()):
            announcement.cancel()
        self._voices.forget_speaker(agent.room_id, agent.agent_id)

    def _announce_in_background(self, agent: DialedAgent, announcement: Awaitable[None]) -> None:
        """Show a started sentence without holding its speaker's answer back behind a browser's blocked write; the
        agent's end cancels the announcement."""
        task = asyncio.ensure_future(announcement)
        pending = self._announcements.setdefault(agent.agent_id, set())
        pending.add(task)
        task.add_done_callback(partial(self._announced, agent.agent_id))

    def _announced(self, agent_id: str, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("agent_announcement_failed", agent_id=agent_id, exc_info=task.exception())
        pending = self._announcements.get(agent_id)
        if pending is not None:
            pending.discard(task)
            if not pending:
                del self._announcements[agent_id]

    def _begin(self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence) -> Awaitable[None]:
        """Start the sentence's length and answer its speaker now; the returned announcement shows it."""
        queue.playing = sentence
        _answer(sentence, sentence.duration_ms)
        delay = sentence.duration_ms / MILLISECONDS_PER_SECOND
        queue.timer = self._scheduler.call_later(delay, partial(self._played, agent, sentence))
        return self._start(agent, sentence)

    async def _played(self, agent: DialedAgent, sentence: QueuedSentence) -> None:
        queue = self._queues.get(agent.agent_id)
        if queue is None or queue.playing is not sentence:
            return
        self._voices.release(sentence.voice.id)
        queue.playing = None
        continues = any(isinstance(waiting, QueuedSentence) for waiting in queue.waiting)
        self._ended(agent, sentence, SentenceEnd.CONTINUED if continues else SentenceEnd.PLAYED)
        await self._advance(agent, queue)

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
        """Post the sentence as a line, then answer its speaker."""
        await self._write(agent, sentence.text)
        _answer(sentence, SpeakFallback.WRITTEN)


def _cancel_timer(queue: _Queue) -> None:
    if queue.timer is not None:
        queue.timer.cancel()


def _answer(sentence: WaitingSentence, answer: SpeakResult) -> None:
    """Tell the sentence's speaker, unless the speaker gave up waiting."""
    if not sentence.answer.done():
        sentence.answer.set_result(answer)
