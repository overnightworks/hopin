import asyncio
import logging
import secrets
from dataclasses import dataclass, field, replace
from functools import partial
from typing import TYPE_CHECKING, Final, Protocol

import structlog
from logline import StepTimer

from hopin.errors import InvalidMessageError
from hopin.game.frames import (
    AnswerCountFrame,
    BusyRefusedFrame,
    EndedFrame,
    GameFrame,
    IdlePhaseFrame,
    LineFrame,
    QuizPhaseFrame,
    RefusedFrame,
    SilentVoiceFrame,
    VoiceFrame,
)
from hopin.game.quiz import QUIZ_ROUNDS, Quiz
from hopin.game.vocabulary import (
    FEATURE_NAME,
    GAME_ANSWER_MAX_CHARACTERS,
    GAME_RUN_DEADLINE_SECONDS,
    SIGNAL_TYPES,
    VOICE_DEADLINE_SECONDS,
    GamePhase,
    GameRefusal,
    GameSignal,
)
from hopin.game.voice import GAME_SPEAKER, GAME_VOICES_KEPT, VOICE_DEADLINE_CUT
from hopin.rooms.reference import room_reference
from hopin.signaling.features import TransitionFeature
from hopin.signaling.replay import Supersedes
from hopin.signaling.scheduler import DeadlineKind
from hopin.signaling.transition import Section, Work

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from starlette.requests import HTTPConnection

    from hopin.game.quiz import Turn
    from hopin.game.voice import StoredVoice, Voice, VoiceShelf
    from hopin.signaling.frames import ForeignFrame, JsonObject, PeerId
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.transition import LiveSeats, RoomView, Transition, WorkRoom

logger: structlog.stdlib.BoundLogger = structlog.get_logger()
_STEPS: Final = logging.getLogger(__name__)

RUN_ENDED_REASON: Final = "ended"
RUN_KEY_BYTES: Final = 8
# How a broken write is logged, by the frames it should have carried.
_RUN_WRITE_BROKE: Final = "game_driver.failed"
_END_WRITE_BROKE: Final = "game_end.failed"
_COUNT_WRITE_BROKE: Final = "game_answer_count.failed"
# A seat a slow write holds back gets only the newest count, and nothing of a run an end has ended.
_A_NEWER_COUNT: Final = Supersedes(frozenset({GameSignal.GAME_ANSWER_COUNT}))
_AN_END: Final = Supersedes(frozenset({GameSignal.GAME_LINE, GameSignal.GAME_VOICE, GameSignal.GAME_ANSWER_COUNT}))


@dataclass(frozen=True)
class Line:
    text: str


@dataclass(frozen=True)
class Refused:
    """Why there is no line; a busy refusal carries the seconds to wait and no other refusal does."""

    reason: GameRefusal
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if (self.reason is GameRefusal.BUSY) != (self.retry_after_seconds is not None):
            raise ValueError(f"a {self.reason.value} refusal carries seconds to wait exactly when it is busy")


type GameOutcome = Line | Refused


class GameMaster(Protocol):
    async def ask(self, starter: HTTPConnection, transcript: tuple[Turn, ...]) -> GameOutcome:
        """The game master's line for the transcript's last request, on the starter's access, or why there is none.

        Raises nothing but `CancelledError`; a cancelled call has closed its connection when it ends.
        """

    async def aclose(self) -> None:
        """Release what the game master holds; called once, when the game shuts down."""


def _refusal_frame(refusal: Refused) -> BusyRefusedFrame | RefusedFrame:
    if refusal.reason is GameRefusal.BUSY:
        assert refusal.retry_after_seconds is not None  # noqa: S101 - Refused's constructor guarantees the wait
        return BusyRefusedFrame(refusal.reason, refusal.retry_after_seconds)
    return RefusedFrame(refusal.reason)


@dataclass(frozen=True)
class _Phase:
    """A room's phase other than idle, and the seats already told it, so each seat is told once."""

    frame: QuizPhaseFrame
    told: frozenset[PeerId] = frozenset()


@dataclass(frozen=True)
class _Run:
    """A run of the room's quiz, which the room keeps until the run's work has ended, so a new quiz never overlaps
    it; an end marks it ended first."""

    key: str
    quiz: Quiz
    ended: bool = False


@dataclass(frozen=True)
class _Table:
    """A room's game: its quiz, its phase and its runs, the oldest first."""

    quiz: Quiz | None = None
    phase: _Phase | None = None
    runs: tuple[_Run, ...] = ()

    def run(self, key: str) -> _Run | None:
        return next((run for run in self.runs if run.key == key), None)

    def is_live(self, key: str) -> bool:
        run = self.run(key)
        return run is not None and not run.ended


TABLE = Section[_Table](FEATURE_NAME)


@dataclass(frozen=True)
class _Delivery:
    """What a run's outcome left to do once its frames are sent: the seats its line reached, and the quiz whose next
    request runs at once."""

    ended: bool = False
    recipients: tuple[PeerId, ...] = ()
    chained: Quiz | None = None


@dataclass(eq=False)
class _LiveRun:
    """A run's work on this process: its calls, which its deadlines and a stop cancel, and its end, which the room's
    next run waits for."""

    ended: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: bool = False
    ask: asyncio.Task[GameOutcome] | None = None
    voice: asyncio.Task[bytes | None] | None = None

    def stop(self) -> None:
        self.stopped = True
        for call in (self.ask, self.voice):
            if call is not None:
                call.cancel()


class Game(TransitionFeature):
    """Runs one quiz per room: its runs one after another, each run's work on the starter's process."""

    def __init__(
        self,
        deadlines: Deadlines,
        game_master: GameMaster,
        topic_drawer: Callable[[], tuple[str, ...]],
        voice: Voice | None,
        voices: VoiceShelf,
    ) -> None:
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.GAME_RUN, self._expire_run)
        deadlines.register(DeadlineKind.GAME_VOICE, self._expire_voice)
        self._topic_drawer = topic_drawer
        self._game_master = game_master
        self._voice = voice
        self._voices = voices
        self._live: dict[str, _LiveRun] = {}
        self._latest: dict[str, _LiveRun] = {}

    @property
    def name(self) -> str:
        return FEATURE_NAME

    @property
    def browser_modules(self) -> tuple[str, ...]:
        return (FEATURE_NAME,)

    @property
    def signal_types(self) -> Mapping[str, str]:
        return SIGNAL_TYPES

    def on_frame(self, view: RoomView, peer_id: PeerId, frame: ForeignFrame, transition: Transition) -> bool:
        if view.room_of(peer_id) is None:
            return frame.signal_type in SIGNAL_TYPES.values()
        match frame.signal_type:
            case GameSignal.GAME_START:
                self._start(view, peer_id, transition)
            case GameSignal.GAME_REVEAL:
                self._reveal(view, peer_id, transition)
            case GameSignal.GAME_ANSWER:
                self._answer(view, peer_id, frame.data, transition)
            case GameSignal.GAME_END:
                self._end(view, peer_id, transition)
            case _:
                return False
        return True

    def _start(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        table = _table_of(view)
        if table.runs:
            transition.send(peer_id, RefusedFrame(GameRefusal.ALREADY_ANSWERING).to_json())
        elif table.quiz is not None:
            transition.send(peer_id, RefusedFrame(GameRefusal.QUIZ_RUNNING).to_json())
        else:
            self._run(table, Quiz.opening(peer_id, self._topic_drawer()), transition)

    def _reveal(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        table = _table_of(view)
        quiz = table.quiz
        if quiz is not None and quiz.may_reveal(peer_id):
            self._run(table, quiz.revealing(), transition)
        elif quiz is None or quiz.starter != peer_id:
            transition.send(peer_id, RefusedFrame(GameRefusal.NOT_STARTER).to_json())

    def _end(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """End the room's quiz for everyone."""
        table = _table_of(view)
        if table.quiz is None:
            return
        if not table.quiz.may_end(peer_id, hosts_call=view.is_host(peer_id)):
            transition.send(peer_id, RefusedFrame(GameRefusal.NOT_STARTER_OR_HOST).to_json())
            return
        _end_for_everyone(view, table, EndedFrame(), transition)

    def _answer(self, view: RoomView, peer_id: PeerId, data: JsonObject, transition: Transition) -> None:
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise InvalidMessageError("An answer needs text")
        table = _table_of(view)
        quiz = table.quiz
        if quiz is None or not quiz.may_answer(peer_id):
            transition.send(peer_id, RefusedFrame(GameRefusal.ANSWER_CLOSED).to_json())
            return
        if len(text.strip()) > GAME_ANSWER_MAX_CHARACTERS:
            transition.send(peer_id, RefusedFrame(GameRefusal.ANSWER_TOO_LONG).to_json())
            return
        answered = quiz.with_answer(peer_id, view.name_of(peer_id), text.strip())
        self._count_and_reveal_when_due(view, replace(table, quiz=answered), transition)

    def _count_and_reveal_when_due(self, view: RoomView, table: _Table, transition: Transition) -> None:
        """Tell each seat the count, then reveal if everyone answered."""
        quiz = table.quiz
        assert quiz is not None  # noqa: S101 - only an answer or a player's leave counts, and both need the quiz
        present = frozenset(player for player in quiz.players if player in view.members)
        transition.put(TABLE, table)
        transition.log_broken_writes_as(_COUNT_WRITE_BROKE)
        for seat in view.members:
            frame = AnswerCountFrame(
                answered=quiz.answered_count(present),
                players=len(quiz.players & present),
                own_answer_counted=True if seat in quiz.counted_players(present) else None,
            )
            transition.send(seat, frame.to_json(), _A_NEWER_COUNT)
        if quiz.everyone_answered(present) and quiz.starter in view.members:
            self._run(table, quiz.revealing(), transition)

    def _run(self, table: _Table, quiz: Quiz, transition: Transition) -> None:
        """Dispatch the quiz's next run; it asks once the room's run before it has ended."""
        run = _Run(secrets.token_hex(RUN_KEY_BYTES), quiz)
        phase = _Phase(QuizPhaseFrame(GamePhase.THINKING, quiz.round, QUIZ_ROUNDS, quiz.starter))
        transition.put(TABLE, _Table(quiz, phase, (*table.runs, run)))
        transition.dispatch(Work(run.key, quiz.starter))

    def seat_joined(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """Tell a seat that just joined its room's phase; a fresh seat gets no question until the next one."""
        table = _table_of(view)
        phase = table.phase
        if phase is not None and peer_id not in phase.told:
            transition.put(TABLE, replace(table, phase=replace(phase, told=phase.told | {peer_id})))
            transition.send(peer_id, phase.frame.to_json())

    def seat_left(self, view: RoomView, peer_id: PeerId, transition: Transition) -> None:
        """The starter's leave ends the quiz for everyone; a player's leave shrinks the count of an answered
        question."""
        table = _table_of(view)
        quiz = table.quiz
        if quiz is None:
            return
        if peer_id == quiz.starter:
            _end_for_everyone(view, table, EndedFrame(leaver=view.name_of(peer_id)), transition)
        elif quiz.counts_leave_of(peer_id):
            self._count_and_reveal_when_due(view, replace(table, quiz=quiz.without_player(peer_id)), transition)

    def room_ended(self, room_id: str) -> None:
        self._voices.forget_speaker(room_id, GAME_SPEAKER)

    async def shut_down(self) -> None:
        await self._game_master.aclose()

    def stop_work(self, key: str) -> None:
        self._deadlines.cancel(DeadlineKind.GAME_RUN, key)
        self._deadlines.cancel(DeadlineKind.GAME_VOICE, key)
        # A work stopped before it began finds its run stopped when it begins.
        self._live.setdefault(key, _LiveRun()).stop()

    async def run_work(self, work: Work, live_seats: LiveSeats, room: WorkRoom) -> None:
        """Run the quiz's run on the starter's socket once the room's run before it has ended: tell the room it
        thinks, ask, deliver the line with the phase it leads to, then its voice."""
        live = self._live.setdefault(work.key, _LiveRun())
        previous, self._latest[room.room_id] = self._latest.get(room.room_id), live
        try:
            with StepTimer("game_master_run.ended", _STEPS, room_ref=room_reference(room.room_id)) as step:
                step.handled()  # its broken writes are logged where they happen
                if previous is not None:
                    with StepTimer("run_wait.ended", _STEPS, room_ref=room_reference(room.room_id)):
                        await previous.ended.wait()
                view = await room.view()
                if work.seat not in view.members:
                    # The starter left before its run began: its seat is gone, and its leave, still to commit, ends
                    # the quiz.
                    live.stop()
                quiz = _asked_quiz(work.key, live, view)
                starter = None
                if quiz is not None:
                    starter = live_seats.connection_of(work.seat)
                    self._start_ask(work.key, live, starter, quiz)
                await room.apply(partial(_tell_thinking, work.key, live))
                outcome = await self._outcome(work.key, live)
                self._start_voice(work.key, live, starter, outcome)
                delivery = await room.apply(partial(self._settle, work.key, live, outcome))
                reason = _refusal_reason(delivery.ended, outcome)
                if reason is not None:
                    step.refuse(reason)
                if live.voice is not None and not delivery.ended:
                    await self._send_voice(room, work.key, live, delivery.recipients)
                if delivery.chained is not None:
                    await room.apply(partial(self._chain, work.key, live, delivery.chained))
        finally:
            await room.apply(partial(_forget, work.key))
            del self._live[work.key]
            if self._latest.get(room.room_id) is live:
                del self._latest[room.room_id]
            live.ended.set()

    def _start_ask(self, key: str, live: _LiveRun, starter: HTTPConnection, quiz: Quiz) -> None:
        live.ask = asyncio.create_task(self._game_master.ask(starter, quiz.transcript))
        self._deadlines.set(DeadlineKind.GAME_RUN, key, GAME_RUN_DEADLINE_SECONDS)

    async def _outcome(self, key: str, live: _LiveRun) -> GameOutcome:
        if live.ask is None:
            return Refused(GameRefusal.UNAVAILABLE)
        await asyncio.wait({live.ask})
        self._deadlines.cancel(DeadlineKind.GAME_RUN, key)
        return _outcome_of(live.ask)

    def _start_voice(self, key: str, live: _LiveRun, starter: HTTPConnection | None, outcome: GameOutcome) -> None:
        """Start making the line's voice on its own deadline as the line is delivered; the line never waits for it."""
        if starter is not None and self._voice is not None and isinstance(outcome, Line) and not live.stopped:
            live.voice = asyncio.create_task(self._voice.speak(starter, outcome.text))
            self._deadlines.set(DeadlineKind.GAME_VOICE, key, VOICE_DEADLINE_SECONDS)

    def _settle(
        self, key: str, live: _LiveRun, outcome: GameOutcome, view: RoomView, transition: Transition
    ) -> _Delivery:
        """Deliver the run's outcome to the seats and move the quiz on by it, the line's recipients its players: the
        line for every seat, or the refusal for the starter and, past the opening, the end for the others; then the
        phase it leads to."""
        table = _table_of(view)
        run = table.run(key)
        if live.stopped or run is None or run.ended:
            return _Delivery(ended=run is not None and run.ended)
        transition.log_broken_writes_as(_RUN_WRITE_BROKE)
        recipients = view.members
        quiz = None
        if isinstance(outcome, Line):
            line = LineFrame(
                outcome.text,
                voiced=True if self._voice is not None else None,
                round=run.quiz.round if run.quiz.is_asking else None,
            )
            _send_each(transition, recipients, line)
            quiz = run.quiz.heard(outcome.text, frozenset(recipients))
        else:
            transition.send(run.quiz.starter, _refusal_frame(outcome).to_json())
            if not run.quiz.is_opening:
                _send_each(transition, (seat for seat in recipients if seat != run.quiz.starter), EndedFrame())
        # Each phase's recipients are read in the same step that sets it, so every seat seated now is told here, a
        # seat that joins later is greeted by `seat_joined`, and idle greets no one.
        chained = None
        if quiz is None or quiz.is_over:
            transition.put(TABLE, replace(table, quiz=None, phase=None))
            _send_each(transition, recipients, IdlePhaseFrame(GamePhase.IDLE))
        elif quiz.awaits_game_master:
            chained = quiz
        else:
            answering = QuizPhaseFrame(GamePhase.ANSWERING, quiz.round, QUIZ_ROUNDS, quiz.starter)
            phase = _Phase(answering, frozenset(recipients))
            transition.put(TABLE, replace(table, quiz=quiz, phase=phase))
            _send_each(transition, recipients, phase.frame)
        return _Delivery(recipients=recipients, chained=chained)

    async def _send_voice(self, room: WorkRoom, key: str, live: _LiveRun, recipients: tuple[PeerId, ...]) -> None:
        """Wait for the line's voice, then send its one frame, made or empty, to the line's recipients still seated
        while the run is live."""
        assert live.voice is not None  # noqa: S101 - only a run whose voice started sends it
        with StepTimer("voice_wait.ended", _STEPS, room_ref=room_reference(room.room_id)):
            await asyncio.wait({live.voice})
        self._deadlines.cancel(DeadlineKind.GAME_VOICE, key)
        if live.stopped:
            return
        frame = self._voice_frame(room.room_id, _voice_of(live.voice))
        await room.apply(partial(_send_voice_frame, key, frame, recipients))

    def _voice_frame(self, room_id: str, wav: bytes | None) -> VoiceFrame | SilentVoiceFrame:
        """The line's voice, or, with none, the word that none follows."""
        if wav is None:
            return SilentVoiceFrame()
        voice = self._keep(room_id, wav)
        return VoiceFrame(voice.id, voice.path)

    def _keep(self, room_id: str, wav: bytes) -> StoredVoice:
        """Keep the sound as the room's latest voice; the voice after the next or the room's end replaces it."""
        voice = self._voices.put(room_id, GAME_SPEAKER, wav)
        self._voices.keep_latest(room_id, GAME_SPEAKER, GAME_VOICES_KEPT)
        return voice

    def _chain(self, key: str, live: _LiveRun, quiz: Quiz, view: RoomView, transition: Transition) -> None:
        """Run the quiz's next request at once, on the starter's token and with no idle in between.

        A starter whose seat is gone gets no run: that seat's `seat_left` notice ends the quiz.
        """
        table = _table_of(view)
        if not live.stopped and table.is_live(key) and quiz.starter in view.members:
            self._run(table, quiz, transition)

    async def _expire_run(self, key: str) -> None:  # NOSONAR(S7503) the deadlines await it
        # A run cancels its deadline before its work ends, so a due deadline finds its call.
        ask = self._live[key].ask
        assert ask is not None  # noqa: S101 - the run's deadline is set with its call
        ask.cancel()

    async def _expire_voice(self, key: str) -> None:  # NOSONAR(S7503) the deadlines await it
        voice = self._live[key].voice
        assert voice is not None  # noqa: S101 - the voice's deadline is set with it and cancelled before it goes
        voice.cancel(VOICE_DEADLINE_CUT)


def _table_of(view: RoomView) -> _Table:
    return view.section(TABLE) or _Table()


def _send_each(
    transition: Transition, recipients: Iterable[PeerId], frame: GameFrame, supersedes: Supersedes | None = None
) -> None:
    wire_frame = frame.to_json()
    for peer_id in recipients:
        transition.send(peer_id, wire_frame, supersedes)


def _end_for_everyone(view: RoomView, table: _Table, notice: EndedFrame, transition: Transition) -> None:
    """Send the room's seats the notice, then idle, and remove the quiz; its runs stop and stay until they end."""
    for run in table.runs:
        transition.stop(run.key)
    transition.put(TABLE, _Table(runs=tuple(replace(run, ended=True) for run in table.runs)))
    transition.log_broken_writes_as(_END_WRITE_BROKE)
    _send_each(transition, view.members, notice, _AN_END)
    _send_each(transition, view.members, IdlePhaseFrame(GamePhase.IDLE))


def _asked_quiz(key: str, live: _LiveRun, view: RoomView) -> Quiz | None:
    """The quiz the run asks for; a stopped run asks nothing."""
    run = _table_of(view).run(key)
    return None if live.stopped or run is None or run.ended else run.quiz


def _tell_thinking(key: str, live: _LiveRun, view: RoomView, transition: Transition) -> None:
    """Tell each seat of the run's room not told yet that the game master thinks, while the run is live."""
    table = _table_of(view)
    if live.stopped or not table.is_live(key) or table.phase is None:
        return
    recipients = [seat for seat in view.members if seat not in table.phase.told]
    transition.put(TABLE, replace(table, phase=replace(table.phase, told=table.phase.told | set(recipients))))
    transition.log_broken_writes_as(_RUN_WRITE_BROKE)
    _send_each(transition, recipients, table.phase.frame)


def _send_voice_frame(
    key: str,
    frame: VoiceFrame | SilentVoiceFrame,
    recipients: tuple[PeerId, ...],
    view: RoomView,
    transition: Transition,
) -> None:
    if _table_of(view).is_live(key):
        transition.log_broken_writes_as(_RUN_WRITE_BROKE)
        _send_each(transition, (seat for seat in recipients if seat in view.members), frame)


def _forget(key: str, view: RoomView, transition: Transition) -> None:
    table = _table_of(view)
    if table.run(key) is not None:
        transition.put(TABLE, replace(table, runs=tuple(run for run in table.runs if run.key != key)))


def _refusal_reason(ended: bool, outcome: GameOutcome) -> str | None:
    """Why the run showed no line, a code from a closed set: an end that cancelled it, else the refusal's own reason."""
    if ended:
        return RUN_ENDED_REASON
    return None if isinstance(outcome, Line) else outcome.reason.value


def _voice_of(voice: asyncio.Task[bytes | None]) -> bytes | None:
    if voice.cancelled():
        return None
    error = voice.exception()
    if error is not None:
        # The port raises nothing but cancellation; anything else is logged by its type, never its text.
        logger.error("game_voice.failed", error_type=type(error).__name__)
        return None
    return voice.result()


def _outcome_of(ask: asyncio.Task[GameOutcome]) -> GameOutcome:
    if ask.cancelled():
        return Refused(GameRefusal.UNAVAILABLE)
    error = ask.exception()
    if error is not None:
        # The port raises nothing but cancellation; anything else is logged by its type, never its text.
        logger.error("game_master.failed", error_type=type(error).__name__)
        return Refused(GameRefusal.UNAVAILABLE)
    return ask.result()
