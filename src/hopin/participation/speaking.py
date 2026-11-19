"""An agent's spoken sentences: one queue per joined agent, in memory only. A sentence starts when the previous one has
ended, its speaker is answered then, and its sound is kept only while it waits or plays. A sentence's sound grows as its
renderer streams it, so a sentence plays while it is rendered; it ends when its length has run out on the scheduler's
clock, counted from its start, or when its stream fails, which cuts it. A sentence whose voice failed before any sound
takes its turn in the same queue and is written as a chat line instead. A stop cuts the playing sentence and drops every
other one of the agent. A hold keeps the playing sentence, if one plays, and the queue behind it from going on: no end
is timed and no sentence starts until a continue lets them go on, or a stop or a sentence that names the hold's id drops
them. The queue keeps the time its playing sentence has played, which a continue counts the rest of its length from."""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, cast

import structlog
from logline import StepTimer

from hopin.participation.chat_window import utf8_length
from hopin.participation.vocabulary import (
    SENTENCES_WAITING_PER_AGENT,
    AgentRefusal,
    FigureMood,
    HoldRefusal,
    SpeakFallback,
    SpeakStarted,
    SpeakStop,
    VoiceFailure,
)
from hopin.rooms.reference import room_reference
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine

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


type SpeakResult = SpeakStarted | AgentRefusal | SpeakStop | SpeakFallback | HoldRefusal
"""That a sentence started to play, that it was written instead, or why it never is said."""

type StopResult = str | AgentRefusal | SpeakStop | HoldRefusal
"""The text of the line a stop cut, or why nothing was cut: nothing played, or only held or waiting sentences were
dropped."""

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
class _Hold:
    """The hold that stands over an agent's queue: its id, and the sentence that had started when it arose, none once
    that one was cut or when only waiting ones were held. A continue lets that sentence play on."""

    id: str
    started: QueuedSentence | None


@dataclass
class _Queue:
    """One agent's sentences, kept from its first one until its speaking ends; the queue itself names that span.
    `stops` counts the stops that dropped sentences, which a sentence still being rendered finds when it is done. While
    `held` stands, no sentence plays. The playing or held sentence has played `played` seconds in the runs before the
    one that began at `resumed_at`, which none does while it is held."""

    agent: DialedAgent
    playing: QueuedSentence | None = None
    waiting: deque[WaitingSentence] = field(default_factory=deque)
    rendering: int = 0
    stops: int = 0
    held: _Hold | None = None
    played: float = 0.0
    resumed_at: float | None = None

    def played_at(self, now: float) -> float:
        """The seconds the playing sentence has played at `now`, on the clock of the deadlines."""
        return self.played if self.resumed_at is None else self.played + now - self.resumed_at


class Speaking:
    """The queues of the joined agents' sentences. `start` shows a sentence that begins to play, `ended` tells how
    the playing one ended, before the next one starts, and `write` posts a sentence that takes its turn as a line.
    `hold_ended` tells that the hold over the agent's queue ended, after the line of the sentence it cut was shown, and
    whether the held sentence plays on. `sound_ended` tells that the sound of a sentence that a hold kept ends at the
    seats.
    `in_own_turn` runs a step of an agent's queue, such as showing a started sentence, without holding its speaker's
    answer back behind a browser's blocked write; each step finds whether an end or a stop came first."""

    def __init__(
        self,
        voices: VoiceStore,
        deadlines: Deadlines,
        start: Callable[[DialedAgent, QueuedSentence], Coroutine[None, None, None]],
        ended: Callable[[DialedAgent, QueuedSentence, SentenceEnd], None],
        write: Callable[[DialedAgent, str], Awaitable[None]],
        hold_ended: Callable[[DialedAgent, bool], None],
        sound_ended: Callable[[DialedAgent], None],
        in_own_turn: Callable[[DialedAgent, Coroutine[None, None, None]], None],
    ) -> None:
        self._voices = voices
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.SENTENCE_END, self._played)
        self._start = start
        self._ended = ended
        self._write = write
        self._hold_ended = hold_ended
        self._sound_ended = sound_ended
        self._in_own_turn = in_own_turn
        self._queues: dict[str, _Queue] = {}

    async def speak(
        self,
        agent: DialedAgent,
        text: str,
        render: Renderer,
        mood: FigureMood | None,
        interrupted: str | None = None,
    ) -> SpeakResult:
        """Begin the sentence's sound, which plays with `mood`, and keep it as it grows, starting the sentence at once
        when the agent is silent, else after the ones before it. A sentence that names the hold over the agent's queue
        drops the held rest, cuts the held sentence that had started and starts at once, once its sound began. Answers
        when it starts that it plays, that it was written when its voice failed before any sound, or why it never
        does: no sound came for a reason that is no failure of the voice, a stop dropped it while it was rendered or
        waited, or an end dropped the agent's queue meanwhile, which a rejoin or a resume does not undo. `interrupted`
        is a hold that stood when the sentence was asked for; a stop or an end drops the sentence too, and a continue
        refuses it as no hold that stands."""
        queue = self._queues.setdefault(agent.agent_id, _Queue(agent))
        stops = queue.stops
        queue.rendering += 1
        try:
            rendered = await render(text, agent.voices)
        finally:
            queue.rendering -= 1
        if rendered is None:
            return AgentRefusal.UNAVAILABLE
        dropped = self._dropped(agent, queue, stops, interrupted)
        if dropped is not None:
            if isinstance(rendered, RenderedSentence):
                await rendered.rest.aclose()
            return dropped
        if interrupted is not None:
            self._drop_all(agent, queue)
            self._sound_ended(agent)
        answer: asyncio.Future[SpeakResult] = asyncio.get_running_loop().create_future()
        sentence: WaitingSentence = (
            WrittenSentence(text, answer)
            if isinstance(rendered, VoiceFailure)
            else self._queued(agent, text, mood, rendered, answer)
        )
        if queue.playing is not None or queue.waiting or queue.held is not None:
            queue.waiting.append(sentence)
            return await _answered_after_waiting(agent, answer)
        if isinstance(sentence, WrittenSentence):
            await self._written(agent, sentence)
        else:
            self._in_own_turn(agent, self._begin(agent, queue, sentence))
        return await answer

    def _dropped(
        self, agent: DialedAgent, queue: _Queue, stops: int, interrupted: str | None
    ) -> AgentRefusal | SpeakStop | HoldRefusal | None:
        if self._queues.get(agent.agent_id) is not queue:
            return AgentRefusal.GONE
        if queue.stops != stops:
            return SpeakStop.STOPPED
        if interrupted is not None and interrupted != self._standing(queue):
            return HoldRefusal.NOT_INTERRUPTED
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

    def stop(self, agent: DialedAgent, interrupted: str | None = None) -> StopResult:
        """Cut the sentence the agent plays, or the held one that had started, and drop every other sentence of it,
        rendered, waiting or held, each answered as stopped, which ends the hold; the cut sentence's text, or that
        nothing but held or waiting sentences were dropped, or why nothing was cut: nothing plays, or `interrupted`
        names a hold that does not stand. The agent speaks again at its next sentence."""
        queue = self._queues.get(agent.agent_id)
        if interrupted is not None and (queue is None or interrupted != self._standing(queue)):
            return HoldRefusal.NOT_INTERRUPTED
        if queue is None or (queue.playing is None and queue.held is None):
            return SpeakStop.NOTHING_PLAYING
        cut = self._drop_all(agent, queue)
        return SpeakStop.DROPPED if cut is None else cut

    def hold_refusal(self, agent: DialedAgent, interrupted: str | None) -> HoldRefusal | None:
        """Why a sentence that names `interrupted`, or no hold, is refused: the agent is held and the sentence names
        none, or the hold it names does not stand."""
        queue = self._queues.get(agent.agent_id)
        if interrupted == (None if queue is None else self._standing(queue)):
            return None
        return HoldRefusal.HELD if interrupted is None else HoldRefusal.NOT_INTERRUPTED

    def is_speaking(self, agent: DialedAgent) -> bool:
        """Whether a sentence of the agent plays, waits or is still being rendered, which counts as waiting."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and (queue.playing is not None or bool(queue.waiting) or queue.rendering > 0)

    def hold(self, agent: DialedAgent, hold_id: str) -> None:
        """Hold the agent under `hold_id`: the playing sentence stops where it is, keeping the time it has played, and
        its end is no longer timed, and no waiting sentence starts, until a continue lets them go on or a stop or a
        sentence that names the id drops them. The agent speaks and is not held."""
        queue = self._queues[agent.agent_id]
        queue.played = queue.played_at(self._deadlines.now())
        queue.resumed_at = None
        self._deadlines.cancel(DeadlineKind.SENTENCE_END, agent.agent_id)
        queue.held = _Hold(hold_id, queue.playing)
        queue.playing = None

    def continue_speaking(self, agent: DialedAgent, interrupted: str) -> HoldRefusal | None:
        """End the hold `interrupted` names: the held sentence that had started plays on, its end timed from the time it
        has played, then the queue; the refusal when no such hold stands."""
        queue = self._queues.get(agent.agent_id)
        if queue is None or interrupted != self._standing(queue):
            return HoldRefusal.NOT_INTERRUPTED
        held = cast("_Hold", queue.held)  # the id names the standing hold
        queue.held = None
        resumed = held.started
        if resumed is not None:
            queue.playing = resumed
            queue.resumed_at = self._deadlines.now()
            self._sized(agent, queue, resumed)
        elif queue.waiting:
            self._in_own_turn(agent, self._advance(agent, queue))
        self._hold_ended(agent, resumed is not None)
        return None

    def _drop_all(self, agent: DialedAgent, queue: _Queue) -> str | None:
        """Cut the playing sentence or the held one that had started and drop every other sentence, which ends a hold;
        the text of the cut sentence, none when none had started."""
        held = queue.held
        cut = queue.playing if held is None else held.started
        self._deadlines.cancel(DeadlineKind.SENTENCE_END, agent.agent_id)
        queue.playing = None
        queue.held = None
        queue.stops += 1
        for sentence in queue.waiting:
            if isinstance(sentence, QueuedSentence):
                self._discard(sentence)
            _answer(sentence, SpeakStop.STOPPED)
        queue.waiting.clear()
        if cut is not None:
            self._discard(cut)
            self._ended(agent, cut, SentenceEnd.CUT)
        if held is not None:
            self._hold_ended(agent, False)
        return None if cut is None else cut.text

    @staticmethod
    def _standing(queue: _Queue) -> str | None:
        return None if queue.held is None else queue.held.id

    def is_full(self, agent: DialedAgent) -> bool:
        """Whether as many sentences already wait behind the agent's playing one or are being rendered for its queue as
        it may keep."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and len(queue.waiting) + queue.rendering >= SENTENCES_WAITING_PER_AGENT

    def speaks(self, agent: DialedAgent) -> bool:
        """Whether the agent's speaking has not ended since its first sentence."""
        return agent.agent_id in self._queues

    def stands(self, agent: DialedAgent, sentence: QueuedSentence) -> bool:
        """Whether the sentence is the one the agent's queue plays now or holds, no end or stop having dropped it."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and (
            queue.playing is sentence or (queue.held is not None and queue.held.started is sentence)
        )

    def is_held(self, agent: DialedAgent) -> bool:
        """Whether a hold stands over the agent's queue."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and queue.held is not None

    def end(self, agent: DialedAgent) -> bool:
        """The agent's removal, leave, pause, lease end or call end: drop its queue and its sounds at once; whether a
        hold stood over it, whose sound the seats still keep paused."""
        queue = self._queues.pop(agent.agent_id, None)
        if queue is not None:
            self._deadlines.cancel(DeadlineKind.SENTENCE_END, agent.agent_id)
            for sentence in queue.waiting:
                if isinstance(sentence, QueuedSentence):
                    sentence.filling.cancel()
                _answer(sentence, AgentRefusal.GONE)
            started = queue.playing if queue.held is None else queue.held.started
            if started is not None:
                started.filling.cancel()
                self._ended(agent, started, SentenceEnd.CUT)
            if queue.held is not None:
                self._hold_ended(agent, False)
        self._voices.forget_speaker(agent.room_id, agent.agent_id)
        return queue is not None and queue.held is not None

    def _begin(self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence) -> Coroutine[None, None, None]:
        """Answer the sentence's speaker now and set its end once its sound's length or failure is known; the returned
        announcement shows it."""
        queue.playing = sentence
        queue.played = 0.0
        queue.resumed_at = self._deadlines.now()
        _answer(sentence, SpeakStarted.SPOKEN)
        if sentence.ended.done():
            self._sized(agent, queue, sentence)
        else:
            sentence.ended.add_done_callback(lambda _ended: self._sized(agent, queue, sentence))
        return self._start(agent, sentence)

    def _sized(self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence) -> None:
        """A sound that came whole ends when its length has run out of the time the sentence has played; one that broke
        off ends, cut, at once; one whose length is not known yet is timed when it is. A held sentence's end is never
        timed: it enters cut when its sound breaks off, and the hold stands over the queue."""
        if self._queues.get(agent.agent_id) is not queue or not sentence.ended.done():
            return
        outcome = sentence.ended.result()
        if queue.held is not None and queue.held.started is sentence:
            if isinstance(outcome, VoiceFailure):
                queue.held.started = None
                self._voices.release(sentence.voice.id)
                self._ended(agent, sentence, SentenceEnd.CUT)
            return
        if queue.playing is not sentence:
            return
        if isinstance(outcome, VoiceFailure):
            self._in_own_turn(agent, self._cut(agent, sentence))
            return
        remaining = outcome / MILLISECONDS_PER_SECOND - queue.played_at(self._deadlines.now())
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
        while queue.waiting and queue.held is None and self._queues.get(agent.agent_id) is queue:
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
