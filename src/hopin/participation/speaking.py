"""An agent's spoken sentences: one queue per joined agent, in memory only. A sentence starts when the previous one's
length has run out on the scheduler's clock, and its sound is kept only while it waits or plays."""

from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final

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


type Renderer = Callable[[str], Awaitable[RenderedSentence | None]]
"""Makes the sound of a sentence for the agent that speaks it, or nothing when it cannot."""


@dataclass(frozen=True)
class QueuedSentence:
    """A sentence that waits or plays: its text, its sound's place in the store and how long it plays."""

    text: str
    voice: StoredVoice
    duration_ms: int


@dataclass
class _Queue:
    playing: QueuedSentence | None = None
    waiting: deque[QueuedSentence] = field(default_factory=deque)
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

    async def enqueue(self, agent: DialedAgent, text: str, rendered: RenderedSentence) -> None:
        """Keep the sound and start the sentence at once when the agent is silent, else after the ones before it."""
        sentence = QueuedSentence(
            text, self._voices.put(agent.room_id, agent.agent_id, rendered.wav), rendered.duration_ms
        )
        queue = self._queues.setdefault(agent.agent_id, _Queue())
        if queue.playing is None:
            await self._begin(agent, queue, sentence)
        else:
            queue.waiting.append(sentence)

    def end(self, agent: DialedAgent) -> None:
        """The agent's removal, leave, pause, lease end or call end: drop its queue and its sounds at once."""
        queue = self._queues.pop(agent.agent_id, None)
        if queue is not None and queue.timer is not None:
            queue.timer.cancel()
        self._voices.forget_speaker(agent.room_id, agent.agent_id)

    async def _begin(self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence) -> None:
        queue.playing = sentence
        delay = sentence.duration_ms / MILLISECONDS_PER_SECOND
        queue.timer = self._scheduler.call_later(delay, partial(self._played, agent, sentence))
        await self._start(agent, sentence)

    async def _played(self, agent: DialedAgent, sentence: QueuedSentence) -> None:
        queue = self._queues.get(agent.agent_id)
        if queue is None or queue.playing is not sentence:
            return
        self._voices.release(sentence.voice.id)
        if queue.waiting:
            await self._begin(agent, queue, queue.waiting.popleft())
        else:
            del self._queues[agent.agent_id]
