import asyncio
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final, Protocol

import structlog

from hopin.game.quiz import QUIZ_ROUNDS, Quiz
from hopin.game.vocabulary import (
    BROWSER_MODULE,
    GAME_RUN_DEADLINE_SECONDS,
    SIGNAL_TYPES,
    WIRE_EXPORTS,
    GamePhase,
    GameRefusal,
    GameSignal,
)
from hopin.signaling.frames import TYPE_KEY, JsonObject, PeerId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from starlette.requests import HTTPConnection

    from hopin.game.quiz import Turn
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import ForeignFrame
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

MILLISECONDS_PER_SECOND: Final = 1000
IDLE_FRAME: Final[JsonObject] = {TYPE_KEY: GameSignal.GAME_PHASE.value, "phase": GamePhase.IDLE.value}


@dataclass(frozen=True)
class Line:
    text: str


@dataclass(frozen=True)
class Refused:
    reason: GameRefusal
    retry_after_seconds: int | None = None


type GameOutcome = Line | Refused


class GameMaster(Protocol):
    async def ask(self, starter: HTTPConnection, transcript: tuple[Turn, ...]) -> GameOutcome:
        """The game master's line for the transcript's last request, on the starter's access, or why there is none.

        Raises nothing but `CancelledError`; a cancelled call has closed its connection when it ends.
        """


def _quiz_phase_frame(phase: GamePhase, quiz: Quiz) -> JsonObject:
    return {
        TYPE_KEY: GameSignal.GAME_PHASE.value,
        "phase": phase.value,
        "round": quiz.round,
        "rounds": QUIZ_ROUNDS,
        "starter": quiz.starter,
    }


def _line_frame(line: Line, quiz: Quiz) -> JsonObject:
    frame: JsonObject = {TYPE_KEY: GameSignal.GAME_LINE.value, "text": line.text}
    if quiz.is_asking:
        frame["round"] = quiz.round
    return frame


def _refusal_frame(refusal: Refused) -> JsonObject:
    frame: JsonObject = {TYPE_KEY: GameSignal.GAME_REFUSED.value, "reason": refusal.reason.value}
    if refusal.retry_after_seconds is not None:
        frame["retry_after_seconds"] = refusal.retry_after_seconds
    return frame


@dataclass(eq=False)
class _Phase:
    """A room's phase other than idle, and the seats already told it, so each seat is told once."""

    frame: JsonObject
    told: set[str] = field(default_factory=set)


@dataclass(eq=False)
class _Run:
    """A room's one run in flight; the record itself is the run's generation."""

    quiz: Quiz
    started_at: float
    ask: asyncio.Task[GameOutcome]
    broken_writes: list[Exception] = field(default_factory=list)
    driver: asyncio.Task[None] = field(init=False)
    deadline: TimerHandle = field(init=False)


class Game:
    """Runs one quiz per room: at most one game master run in flight, its frames to the room's seats in order."""

    def __init__(self, rooms: CallRooms, scheduler: Scheduler, game_master: GameMaster) -> None:
        self._rooms = rooms
        self._scheduler = scheduler
        self._game_master = game_master
        self._quizzes: dict[str, Quiz] = {}
        self._runs: dict[str, _Run] = {}
        self._phases: dict[str, _Phase] = {}
        self._drivers: set[asyncio.Task[None]] = set()

    @property
    def signal_types(self) -> Mapping[str, str]:
        return SIGNAL_TYPES

    @property
    def wire_exports(self) -> Mapping[str, Mapping[str, str]]:
        return WIRE_EXPORTS

    @property
    def browser_module(self) -> str:
        return BROWSER_MODULE

    async def on_frame(self, peer_id: PeerId, frame: ForeignFrame) -> bool:
        match frame.signal_type:
            case GameSignal.GAME_START:
                await self._start(peer_id)
            case GameSignal.GAME_REVEAL:
                await self._reveal(peer_id)
            case _:
                return False
        return True

    async def _start(self, peer_id: PeerId) -> None:
        room_id = self._rooms.room_of(peer_id)
        if room_id is None:
            return
        if room_id in self._runs:
            await self._rooms.send(peer_id, _refusal_frame(Refused(GameRefusal.ALREADY_ANSWERING)))
        elif room_id in self._quizzes:
            await self._rooms.send(peer_id, _refusal_frame(Refused(GameRefusal.QUIZ_RUNNING)))
        else:
            self._run(room_id, Quiz(peer_id))

    async def _reveal(self, peer_id: PeerId) -> None:
        room_id = self._rooms.room_of(peer_id)
        if room_id is None:
            return
        quiz = self._quizzes.get(room_id)
        if quiz is not None and quiz.may_reveal(peer_id):
            self._run(room_id, quiz.revealing())
        elif quiz is None or quiz.starter != peer_id:
            await self._rooms.send(peer_id, _refusal_frame(Refused(GameRefusal.NOT_STARTER)))

    def _run(self, room_id: str, quiz: Quiz) -> None:
        # The quiz, the run, its tasks and its deadline exist before any await, so a second tap already finds them.
        starter = self._rooms.connection_of(quiz.starter)
        run = _Run(
            quiz=quiz,
            started_at=self._scheduler.now(),
            ask=asyncio.create_task(self._game_master.ask(starter, quiz.transcript)),
        )
        run.driver = asyncio.create_task(self._drive(room_id, run))
        run.deadline = self._scheduler.call_later(GAME_RUN_DEADLINE_SECONDS, partial(_expire, run))
        self._quizzes[room_id] = quiz
        self._runs[room_id] = run
        self._phases[room_id] = _Phase(_quiz_phase_frame(GamePhase.THINKING, quiz))
        self._drivers.add(run.driver)
        run.driver.add_done_callback(self._driver_finished)

    async def seat_joined(self, peer_id: PeerId) -> None:
        """Tell a seat that just joined its room's phase; a fresh seat gets no question until the next one."""
        room_id = self._rooms.room_of(peer_id)
        phase = None if room_id is None else self._phases.get(room_id)
        if phase is not None and peer_id not in phase.told:
            phase.told.add(peer_id)
            await self._rooms.send(peer_id, phase.frame)

    def room_ended(self, room_id: str) -> None:
        """Forget the room's quiz and run; the run's driver finishes on its own and sends nothing more."""
        self._quizzes.pop(room_id, None)
        self._phases.pop(room_id, None)
        run = self._runs.pop(room_id, None)
        if run is None:
            return
        run.deadline.cancel()
        run.ask.cancel()

    async def shut_down(self) -> None:
        for room_id in self._runs.copy():
            self.room_ended(room_id)
        if self._drivers:
            await asyncio.wait(self._drivers)

    async def _drive(self, room_id: str, run: _Run) -> None:
        # Never cancelled, so no socket write is cut short; the room's end only cancels `ask`. A seat's
        # broken write is held until the run is delivered, so the room keeps the run until `ask` has ended.
        try:
            await self._announce_thinking(room_id, run)
            await asyncio.wait({run.ask})
            outcome = _outcome_of(run.ask)
            run.deadline.cancel()
            self._log_run(room_id, run, outcome)
            await self._deliver(room_id, run, outcome)
        finally:
            if self._runs.get(room_id) is run:
                del self._runs[room_id]
        if run.broken_writes:
            raise run.broken_writes[0]

    async def _deliver(self, room_id: str, run: _Run, outcome: GameOutcome) -> None:
        if self._runs.get(room_id) is not run:
            return
        if isinstance(outcome, Line):
            await self._send_to_room(room_id, run, _line_frame(outcome, run.quiz))
            quiz = run.quiz.answered(outcome.text)
        else:
            await self._send_each(room_id, run, [run.quiz.starter], _refusal_frame(outcome))
            quiz = None
        if self._runs.get(room_id) is not run:
            return
        # Each phase's recipients are read in the same step that sets it, so every seat seated now is told
        # here, a seat that joins later is greeted by `seat_joined`, and idle greets no one.
        if quiz is None or quiz.is_over:
            await self._end_quiz(room_id, run)
        else:
            await self._open_question(room_id, run, quiz)

    async def _open_question(self, room_id: str, run: _Run, quiz: Quiz) -> None:
        self._quizzes[room_id] = quiz
        seated = self._rooms.members(room_id)
        phase = _Phase(_quiz_phase_frame(GamePhase.ANSWERING, quiz), told=set(seated))
        self._phases[room_id] = phase
        await self._send_each(room_id, run, seated, phase.frame)

    async def _end_quiz(self, room_id: str, run: _Run) -> None:
        del self._quizzes[room_id]
        del self._phases[room_id]
        await self._send_to_room(room_id, run, IDLE_FRAME)

    async def _announce_thinking(self, room_id: str, run: _Run) -> None:
        # A seat greeted while the run was starting already holds its thinking frame; each seat gets one.
        if self._runs.get(room_id) is not run:
            return
        phase = self._phases[room_id]
        recipients = [peer_id for peer_id in self._rooms.members(room_id) if peer_id not in phase.told]
        phase.told.update(recipients)
        await self._send_each(room_id, run, recipients, phase.frame)

    async def _send_to_room(self, room_id: str, run: _Run, frame: JsonObject) -> None:
        # Each caller has checked, in this same step, that the run is still the room's, so the room exists.
        await self._send_each(room_id, run, self._rooms.members(room_id), frame)

    async def _send_each(self, room_id: str, run: _Run, recipients: list[str], frame: JsonObject) -> None:
        for peer_id in recipients:
            if self._runs.get(room_id) is not run:
                return
            try:
                await self._rooms.send(peer_id, frame)
            except Exception as error:  # re-raised by the driver once every other seat has its frames
                run.broken_writes.append(error)

    def _log_run(self, room_id: str, run: _Run, outcome: GameOutcome) -> None:
        shown = "line" if isinstance(outcome, Line) else outcome.reason.value
        duration_ms = round((self._scheduler.now() - run.started_at) * MILLISECONDS_PER_SECOND)
        logger.info("game_master_run", room_id=room_id, outcome=shown, duration_ms=duration_ms)

    def _driver_finished(self, driver: asyncio.Task[None]) -> None:
        self._drivers.discard(driver)
        if not driver.cancelled() and (error := driver.exception()) is not None:
            logger.error("game_driver_failed", error=type(error).__name__)


async def _expire(run: _Run) -> None:  # NOSONAR(S7503) the scheduler's contract takes a coroutine function
    # A run that already ended has a finished `ask`, so the cancel is a no-op then.
    run.ask.cancel()


def _outcome_of(ask: asyncio.Task[GameOutcome]) -> GameOutcome:
    if ask.cancelled():
        return Refused(GameRefusal.UNAVAILABLE)
    error = ask.exception()
    if error is not None:
        # The port raises nothing but cancellation; anything else is logged by its type, never its text.
        logger.error("game_master_raised", error=type(error).__name__)
        return Refused(GameRefusal.UNAVAILABLE)
    return ask.result()
