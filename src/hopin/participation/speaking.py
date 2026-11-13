"""An agent's spoken sentences: one queue per joined agent, in memory only. A sentence starts when the previous one's
length has run out on the scheduler's clock, its speaker is answered then, and its sound is kept only while it waits or
plays."""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final

from hopin.participation.vocabulary import SENTENCES_WAITING_PER_AGENT, AgentRefusal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hopin.participation.feature import DialedAgent
    from hopin.participation.voice import StoredVoice, VoiceStore
    from hopin.signaling.scheduler import Scheduler, TimerHandle

MILLISECONDS_PER_SECOND: Final = 1000


@dataclass(frozen=True)
class RenderedSentence:
    """A sentence's sound, as the renderer made it, and how long it plays."""

    wav: bytes = field(repr=False)
    duration_ms: int


type Renderer = Callable[[str, str | None], Awaitable[RenderedSentence | None]]
"""Makes the sound of a sentence in the voice its agent kept at accept (none for the configured one), or nothing when
it cannot."""


@dataclass(frozen=True)
class QueuedSentence:
    """A sentence that waits or plays: its text, its sound's place in the store, how long it plays and its speaker's
    answer, which is how long it plays once it starts, or why it never does."""

    text: str
    voice: StoredVoice
    duration_ms: int
    answer: asyncio.Future[int | AgentRefusal] = field(repr=False, compare=False)


@dataclass
class _Queue:
    """One agent's sentences, kept from its first one until its speaking ends; the queue itself names that span."""

    playing: QueuedSentence | None = None
    waiting: deque[QueuedSentence] = field(default_factory=deque)
    rendering: int = 0
    timer: TimerHandle | None = None


class Speaking:
    """The queues of the joined agents' sentences. `start` shows a sentence that begins to play."""

    def __init__(
        self,
        voices: VoiceStore,
        scheduler: Scheduler,
        start: Callable[[DialedAgent, QueuedSentence], Awaitable[None]],
    ) -> None:
        self._voices = voices
        self._scheduler = scheduler
        self._start = start
        self._queues: dict[str, _Queue] = {}
        self._announcements: dict[str, set[asyncio.Task[None]]] = {}

    async def speak(self, agent: DialedAgent, text: str, render: Renderer) -> int | AgentRefusal:
        """Render the sentence and keep its sound, starting it at once when the agent is silent, else after the ones
        before it. Answers when it starts how long it plays, or why it never does: no sound came, or an end dropped the
        agent's queue while it was rendered or waited, which a rejoin or a resume does not undo."""
        queue = self._queues.setdefault(agent.agent_id, _Queue())
        queue.rendering += 1
        try:
            rendered = await render(text, agent.voice)
        finally:
            queue.rendering -= 1
        if rendered is None:
            return AgentRefusal.UNAVAILABLE
        if self._queues.get(agent.agent_id) is not queue:
            return AgentRefusal.GONE
        sentence = QueuedSentence(
            text,
            self._voices.put(agent.room_id, agent.agent_id, rendered.wav),
            rendered.duration_ms,
            asyncio.get_running_loop().create_future(),
        )
        if queue.playing is None:
            self._announce_in_background(agent, self._begin(agent, queue, sentence))
        else:
            queue.waiting.append(sentence)
        return await sentence.answer

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
            if queue.timer is not None:
                queue.timer.cancel()
            for sentence in queue.waiting:
                _answer(sentence, AgentRefusal.GONE)
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
        if queue.waiting:
            await self._begin(agent, queue, queue.waiting.popleft())


def _answer(sentence: QueuedSentence, answer: int | AgentRefusal) -> None:
    """Tell the sentence's speaker, unless the speaker gave up waiting."""
    if not sentence.answer.done():
        sentence.answer.set_result(answer)
