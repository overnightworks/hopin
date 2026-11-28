"""An agent's spoken sentences: one floor of the agents library (`agents.floor`) per joined agent, in memory only, which
orders them and holds them. A sentence starts when the previous one has ended, its speaker is answered then, and its
sound is kept only while it waits or plays. A sentence's sound grows as its renderer streams it, so a sentence plays
while it is rendered; it ends when its length has run out on the scheduler's clock, counted from its start, or when its
stream fails, which cuts it. A sentence whose voice failed before any sound takes its turn in the same order and is
written as a chat line instead. A stop cuts the playing sentence and drops every other one of the agent, those still
being rendered included. A hold keeps the playing sentence, if one plays, and the ones behind it from going on: no end
is timed and no sentence starts until a continue lets them go on, or a stop or a sentence that names the hold's id drops
them. The queue keeps the time its playing sentence has played, which a continue counts the rest of its length from."""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, cast

import structlog
from agents.floor import AfterAnEnding, Drop, Floor, HoldEnding, PlayOn, Start
from agents.member import (
    ContinueSpeakingRefusal,
    DroppedSpeaking,
    SpeakRefusal,
    StopSpeakingRefusal,
    hold_place,
)
from logline import StepTimer

from hopin.participation.chat_window import utf8_length
from hopin.participation.vocabulary import (
    SENTENCES_WAITING_PER_AGENT,
    AgentRefusal,
    FigureMood,
    HoldEnd,
    SpeakFallback,
    SpeakStarted,
    VoiceFailure,
)
from hopin.rooms.reference import room_reference
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine

    from hopin.participation.participants import DialedAgent
    from hopin.participation.voice import StoredVoice, VoiceStore
    from hopin.signaling.frames import PeerId
    from hopin.signaling.scheduler import Deadlines

MILLISECONDS_PER_SECOND: Final = 1000


def milliseconds(seconds: float) -> int:
    return round(seconds * MILLISECONDS_PER_SECOND)


logger: structlog.stdlib.BoundLogger = structlog.get_logger()
_STEPS: Final = logging.getLogger(__name__)


def speak_timer(agent: DialedAgent, text: str, *, seq: int | None) -> StepTimer:
    """The timer of the `speak.ended` line of one sentence the agent's speaking asked for, started now; `seq` is the
    newest line of the agent's window when the sentence arrived, which the agent's last pulse told. The line ends at
    the sentence's first sound from speech. It runs in the trace of the `traceparent` the speak request carries, as a
    header or in its MCP `_meta`, so it joins the turn that sent the request; a speak without one runs in a trace of
    its own and joins no turn."""
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


type SpeakResult = SpeakStarted | AgentRefusal | SpeakRefusal | SpeakFallback
"""That a sentence started to play, that it was written instead, or why it never is said."""

type StopResult = str | AgentRefusal | StopSpeakingRefusal | DroppedSpeaking
"""The text of the line a stop cut, or why nothing was cut: nothing played, waited or rendered, or only held, waiting
or rendered sentences were dropped."""

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


@dataclass(frozen=True)
class HeldSentence:
    """The sentence a standing hold keeps, which had started and was not cut: the hold's id, the sentence's voice and
    the seconds it had played when the hold arose, on the server's clock."""

    hold_id: str
    voice_id: str
    played_seconds: float


@dataclass(frozen=True)
class EndedHold:
    """A hold that ended and how: its id, the seconds its started sentence had played, none when no sentence had
    started, and the timer of its line, which the receiver ends."""

    id: str
    ending: HoldEnd
    played_seconds: float | None
    timer: StepTimer


def _floor() -> Floor[WaitingSentence]:
    """A person who is still speaking when a hold ends has begun before it, so they do not hold the agent again."""
    return Floor(after_an_ending=AfterAnEnding.BEGAN_BEFORE)


def _spoken(sentence: WaitingSentence | None) -> QueuedSentence | None:
    """The sentence as the spoken one it is: a written sentence ends at its start, so one that plays or is held was
    voiced."""
    return cast("QueuedSentence | None", sentence)


@dataclass
class _Queue:
    """One agent's sentences, kept from its first one until its speaking ends; the queue itself names that span. Its
    `floor` orders them and holds them. The playing or held sentence has played `played` seconds in the runs before the
    one that began at `resumed_at`, which none does while it is held. `hold_timer` is the step of the `agent_hold.ended`
    line of the hold that stands, which the end of the hold hands on."""

    agent: DialedAgent
    floor: Floor[WaitingSentence] = field(default_factory=_floor)
    hold_timer: StepTimer | None = None
    played: float = 0.0
    resumed_at: float | None = None

    def played_at(self, now: float) -> float:
        """The seconds the playing sentence has played at `now`, on the clock of the deadlines."""
        return self.played if self.resumed_at is None else self.played + now - self.resumed_at

    @property
    def started(self) -> QueuedSentence | None:
        """The sentence that started and has not ended, the held one while a hold stands."""
        return _spoken(self.floor.playing)

    def plays(self, sentence: QueuedSentence) -> bool:
        """Whether the sentence plays now: it started and no hold keeps it."""
        return self.floor.plays and self.floor.playing is sentence


class Turn(StrEnum):
    """What a person's turn tells the floor of an agent that hears them: it began and its voice lasted the bound, the
    words it has said so far, its finished line (which ends it) or its end."""

    BEGAN = "began"
    WORDS = "words"
    LINE = "line"
    ENDED = "ended"


class Speaking:
    """The floors of the joined agents' sentences. `start` shows a sentence that begins to play, `ended` tells how
    the playing one ended, before the next one starts, and `write` posts a sentence that takes its turn as a line.
    `start` also says whether the sentence starts at once after a sentence that was continued into it. `hold_ended`
    tells that the hold over the agent's queue ended and how, after the line of the sentence it cut was shown.
    `sound_ended` tells that the sound of a sentence that a hold kept ends at the seats.
    `in_own_turn` runs a step of an agent's queue, such as showing a started sentence, without holding its speaker's
    answer back behind a browser's blocked write; each step finds whether an end or a stop came first."""

    def __init__(
        self,
        voices: VoiceStore,
        deadlines: Deadlines,
        start: Callable[[DialedAgent, QueuedSentence, bool], Coroutine[None, None, None]],
        ended: Callable[[DialedAgent, QueuedSentence, SentenceEnd], None],
        write: Callable[[DialedAgent, str], Awaitable[None]],
        hold_ended: Callable[[DialedAgent, EndedHold], None],
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
        floor = queue.floor
        ticket = floor.render_begins()
        try:
            rendered = await render(text, agent.voices)
        finally:
            refusal = floor.render_ended(ticket, interrupted)
        if rendered is None:
            return AgentRefusal.UNAVAILABLE
        dropped = AgentRefusal.GONE if self._queues.get(agent.agent_id) is not queue else refusal
        if dropped is not None:
            if isinstance(rendered, RenderedSentence):
                await rendered.rest.aclose()
            return dropped
        if interrupted is not None:
            self._answers_the_hold(agent, queue, interrupted)
            self._sound_ended(agent)
        answer: asyncio.Future[SpeakResult] = asyncio.get_running_loop().create_future()
        sentence: WaitingSentence = (
            WrittenSentence(text, answer)
            if isinstance(rendered, VoiceFailure)
            else self._queued(agent, text, mood, rendered, answer)
        )
        starts_at_once = floor.playing is None and not floor.waiting and not floor.holding
        floor.enter(sentence)
        if not starts_at_once:
            return await _answered_after_waiting(agent, answer)
        floor.next_start()
        if isinstance(sentence, WrittenSentence):
            await self._written(agent, queue, sentence)
        else:
            self._in_own_turn(agent, self._begin(agent, queue, sentence))
        return await answer

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
        nothing but held, waiting or rendered sentences were dropped, or why nothing was cut: nothing plays, waits or
        renders, or `interrupted` names a hold that does not stand. The agent speaks again at its next sentence."""
        queue = self._queues.get(agent.agent_id)
        if queue is None:
            return StopSpeakingRefusal.NOTHING_PLAYING if interrupted is None else StopSpeakingRefusal.NOT_INTERRUPTED
        started = queue.started
        hold_id = queue.floor.hold_id
        stopped = queue.floor.stop(interrupted, hold_place(None if started is None else started.text, None))
        if isinstance(stopped, StopSpeakingRefusal):
            return stopped
        self._carry_out(agent, queue, stopped)
        self._hold_over(agent, queue, hold_id, HoldEnd.STOPPED, started=stopped.sentence is not None)
        return DroppedSpeaking(dropped=True) if stopped.cut is None else stopped.cut

    def speak_refusal(self, agent: DialedAgent, interrupted: str | None) -> SpeakRefusal | None:
        """Why a sentence that names `interrupted`, or no hold, is refused: the agent is held and the sentence names
        none, or the hold it names does not stand."""
        queue = self._queues.get(agent.agent_id)
        if queue is None:
            return None if interrupted is None else SpeakRefusal.NOT_INTERRUPTED
        return queue.floor.speak_refusal(interrupted)

    def held_sentence(self, agent: DialedAgent) -> HeldSentence | None:
        """The sentence the agent's standing hold keeps, none while it is not held or when no sentence had started or a
        voice failure cut it."""
        queue = self._queues.get(agent.agent_id)
        if queue is None or (hold_id := queue.floor.hold_id) is None or (started := queue.started) is None:
            return None
        return HeldSentence(hold_id, started.voice.id, queue.played)

    def hold_of(self, agent: DialedAgent) -> str | None:
        """The id of the hold that stands over the agent's queue, none while it is not held."""
        queue = self._queues.get(agent.agent_id)
        return None if queue is None else queue.floor.hold_id

    def plays(self, agent: DialedAgent) -> bool:
        """Whether a sentence of the agent plays now: not one that waits, is held or is still being rendered."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and queue.floor.plays

    def is_speaking(self, agent: DialedAgent) -> bool:
        """Whether a sentence of the agent plays, waits or is still being rendered, which counts as waiting."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and (
            queue.floor.playing is not None or bool(queue.floor.waiting) or queue.floor.renders > 0
        )

    def turn(self, agent: DialedAgent, key: PeerId, event: Turn, text: str = "") -> bool:
        """Tell the agent's floor what a person's turn did, `text` being its words or its finished line; whether a hold
        arises with it. An agent that never spoke has no floor, and the people speaking then are counted when it has."""
        queue = self._queues.get(agent.agent_id)
        if queue is None:
            return False
        floor = queue.floor
        if event is Turn.BEGAN:
            floor.utterance_began(key)
            return floor.hear_partial(key, "")
        if event is Turn.WORDS:
            return floor.hear_partial(key, text)
        if event is Turn.LINE:
            floor.hear_finished(key, text)
        floor.utterance_ended(key)
        return False

    def hold(self, agent: DialedAgent, hold_id: str) -> None:
        """Hold the agent under `hold_id`: the playing sentence stops where it is, keeping the time it has played, and
        its end is no longer timed, and no waiting sentence starts, until a continue lets them go on or a stop or a
        sentence that names the id drops them. The agent speaks and is not held."""
        queue = self._queues[agent.agent_id]
        queue.played = queue.played_at(self._deadlines.now())
        queue.resumed_at = None
        self._deadlines.cancel(DeadlineKind.SENTENCE_END, agent.agent_id)
        queue.hold_timer = StepTimer(
            "agent_hold.ended", _STEPS, room_ref=room_reference(agent.room_id), agent_id=agent.agent_id
        )
        started = queue.started
        queue.floor.sound_stopped_at(hold_place(None if started is None else started.text, None), hold_id)

    def continue_speaking(self, agent: DialedAgent, interrupted: str) -> ContinueSpeakingRefusal | None:
        """End the hold `interrupted` names: the held sentence that had started plays on, its end timed from the time it
        has played, then the queue; the refusal when no such hold stands."""
        queue = self._queues.get(agent.agent_id)
        if queue is None:
            return ContinueSpeakingRefusal.NOT_INTERRUPTED
        steps = queue.floor.continue_speaking(interrupted)
        if isinstance(steps, ContinueSpeakingRefusal):
            return steps
        resumed = _spoken(next((step.sentence for step in steps if isinstance(step, PlayOn)), None))
        if resumed is not None:
            queue.resumed_at = self._deadlines.now()
            self._sized(agent, queue, resumed)
        elif queue.floor.waiting:
            self._in_own_turn(agent, self._advance(agent, queue))
        self._hold_over(agent, queue, interrupted, HoldEnd.CONTINUED, started=resumed is not None)
        return None

    def _answers_the_hold(self, agent: DialedAgent, queue: _Queue, hold_id: str) -> None:
        """A sentence that names the hold answers it: the held rest is dropped and the hold is over."""
        steps = queue.floor.end_hold(HoldEnding.ANSWERED)
        queue.floor.answer_goes_on()
        dropped = next((step for step in steps if isinstance(step, Drop)), Drop(None, (), None))
        self._carry_out(agent, queue, dropped)
        self._hold_over(agent, queue, hold_id, HoldEnd.ANSWERED, started=dropped.sentence is not None)

    def _hold_over(
        self, agent: DialedAgent, queue: _Queue, hold_id: str | None, ending: HoldEnd, *, started: bool
    ) -> None:
        """The hold `hold_id` ended, if one stood: its end is told, with the seconds its started sentence had played."""
        if hold_id is None:
            return
        timer = queue.hold_timer
        assert timer is not None  # noqa: S101 - a hold stands only once `hold` started its timer
        queue.hold_timer = None
        self._hold_ended(agent, EndedHold(hold_id, ending, queue.played if started else None, timer))

    def _carry_out(self, agent: DialedAgent, queue: _Queue, dropped: Drop[WaitingSentence]) -> None:
        """Cut the started sentence and drop every waiting one, each answered stopped, as a stop or an answer to the
        hold decided; the sound of each is gone."""
        self._deadlines.cancel(DeadlineKind.SENTENCE_END, agent.agent_id)
        for sentence in dropped.waiting:
            if isinstance(sentence, QueuedSentence):
                self._discard(sentence)
            _answer(sentence, SpeakRefusal.STOPPED)
        if (cut := _spoken(dropped.sentence)) is not None:
            self._discard(cut)
            queue.floor.sentence_ended()
            self._ended(agent, cut, SentenceEnd.CUT)

    def is_full(self, agent: DialedAgent) -> bool:
        """Whether as many sentences already wait behind the agent's playing one or are being rendered for its queue as
        it may keep."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and len(queue.floor.waiting) + queue.floor.renders >= SENTENCES_WAITING_PER_AGENT

    def speaks(self, agent: DialedAgent) -> bool:
        """Whether the agent's speaking has not ended since its first sentence."""
        return agent.agent_id in self._queues

    def stands(self, agent: DialedAgent, sentence: QueuedSentence) -> bool:
        """Whether the sentence is the one the agent's queue plays now or holds, no end or stop having dropped it."""
        queue = self._queues.get(agent.agent_id)
        return queue is not None and queue.floor.playing is sentence

    def end(self, agent: DialedAgent) -> bool:
        """The agent's removal, leave, pause, lease end or call end: drop its queue and its sounds at once; whether a
        hold stood over it, whose sound the seats still keep paused."""
        queue = self._queues.pop(agent.agent_id, None)
        hold_id = None if queue is None else queue.floor.hold_id
        if queue is not None:
            self._deadlines.cancel(DeadlineKind.SENTENCE_END, agent.agent_id)
            for sentence in queue.floor.waiting:
                if isinstance(sentence, QueuedSentence):
                    sentence.filling.cancel()
                _answer(sentence, AgentRefusal.GONE)
            started = queue.started
            if started is not None:
                started.filling.cancel()
                self._ended(agent, started, SentenceEnd.CUT)
            self._hold_over(agent, queue, hold_id, HoldEnd.ENDED, started=started is not None)
        self._voices.forget_speaker(agent.room_id, agent.agent_id)
        return hold_id is not None

    def _begin(
        self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence, *, at_once: bool = False
    ) -> Coroutine[None, None, None]:
        """Answer the sentence's speaker now and set its end once its sound's length or failure is known; the returned
        announcement shows it. `at_once` says it starts the moment a sentence continued into it."""
        queue.played = 0.0
        queue.resumed_at = self._deadlines.now()
        _answer(sentence, SpeakStarted.SPOKEN)
        if sentence.ended.done():
            self._sized(agent, queue, sentence)
        else:
            sentence.ended.add_done_callback(lambda _ended: self._sized(agent, queue, sentence))
        return self._start(agent, sentence, at_once)

    def _sized(self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence) -> None:
        """A sound that came whole ends when its length has run out of the time the sentence has played; one that broke
        off ends, cut, at once; one whose length is not known yet is timed when it is. A held sentence's end is never
        timed: it enters cut when its sound breaks off, and the hold stands over the queue."""
        if self._queues.get(agent.agent_id) is not queue or not sentence.ended.done():
            return
        outcome = sentence.ended.result()
        if queue.floor.holding and queue.floor.playing is sentence:
            if isinstance(outcome, VoiceFailure):
                queue.floor.sentence_ended()
                self._voices.release(sentence.voice.id)
                self._ended(agent, sentence, SentenceEnd.CUT)
            return
        if not queue.plays(sentence):
            return
        if isinstance(outcome, VoiceFailure):
            self._in_own_turn(agent, self._cut(agent, sentence))
            return
        remaining = outcome / MILLISECONDS_PER_SECOND - queue.played_at(self._deadlines.now())
        self._deadlines.set(DeadlineKind.SENTENCE_END, agent.agent_id, max(remaining, 0))

    async def _played(self, agent_id: str) -> None:
        queue = self._queues[agent_id]
        sentence = queue.started
        assert sentence is not None  # noqa: S101 - every end of the playing sentence cancels its deadline
        continues = any(isinstance(waiting, QueuedSentence) for waiting in queue.floor.waiting)
        await self._finish(queue.agent, queue, sentence, SentenceEnd.CONTINUED if continues else SentenceEnd.PLAYED)

    async def _cut(self, agent: DialedAgent, sentence: QueuedSentence) -> None:
        queue = self._queues.get(agent.agent_id)
        if queue is None or not queue.plays(sentence):
            return
        await self._finish(agent, queue, sentence, SentenceEnd.CUT)

    async def _finish(self, agent: DialedAgent, queue: _Queue, sentence: QueuedSentence, end: SentenceEnd) -> None:
        self._voices.release(sentence.voice.id)
        queue.floor.sentence_ended()
        self._ended(agent, sentence, end)
        await self._advance(agent, queue, at_once=end is SentenceEnd.CONTINUED)

    def _discard(self, sentence: QueuedSentence) -> None:
        """Stop rendering the sentence and drop its sound."""
        sentence.filling.cancel()
        self._voices.release(sentence.voice.id)

    async def _advance(self, agent: DialedAgent, queue: _Queue, *, at_once: bool = False) -> None:
        """Take the waiting sentences in turn as the floor starts them: write each one whose voice failed and start the
        first that has a sound, unless an end or a stop dropped them meanwhile. `at_once` says the first sound starts
        the moment a sentence continued into it, which a written one between does not change."""
        while self._queues.get(agent.agent_id) is queue and isinstance(started := queue.floor.next_start(), Start):
            following = started.sentence
            if isinstance(following, QueuedSentence):
                await self._begin(agent, queue, following, at_once=at_once)
                return
            await self._written(agent, queue, following)

    async def _written(self, agent: DialedAgent, queue: _Queue, sentence: WrittenSentence) -> None:
        """Post the sentence as a line, then answer its speaker; a write that fails fails the speaker's call instead,
        so the answer never claims a line that did not stand, and the queue goes on. The floor is told it ended at once,
        for no sound plays."""
        queue.floor.sentence_ended()
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
        if isinstance(result, AgentRefusal | SpeakRefusal):
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
