import asyncio
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final, Protocol

import structlog

from hopin.errors import InvalidMessageError
from hopin.game.quiz import QUIZ_ROUNDS, Quiz
from hopin.game.vocabulary import (
    BROWSER_MODULE,
    GAME_ANSWER_MAX_CHARACTERS,
    GAME_RUN_DEADLINE_SECONDS,
    SIGNAL_TYPES,
    WIRE_EXPORTS,
    GamePhase,
    GameRefusal,
    GameSignal,
)
from hopin.signaling.frames import TYPE_KEY, JsonObject, PeerId

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from starlette.requests import HTTPConnection

    from hopin.game.quiz import Turn
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import ForeignFrame
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

MILLISECONDS_PER_SECOND: Final = 1000
IDLE_FRAME: Final[JsonObject] = {TYPE_KEY: GameSignal.GAME_PHASE.value, "phase": GamePhase.IDLE.value}
ENDED_FRAME: Final[JsonObject] = {TYPE_KEY: GameSignal.GAME_ENDED.value}


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


def _answer_count_frame(quiz: Quiz, present: frozenset[PeerId]) -> JsonObject:
    return {
        TYPE_KEY: GameSignal.GAME_ANSWER_COUNT.value,
        "answered": quiz.answered_count(present),
        "players": len(quiz.players & present),
    }


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
    """A room's run; the record itself is the run's generation, and it starts once its previous run has ended."""

    quiz: Quiz
    previous: asyncio.Task[None] | None
    ask: asyncio.Task[GameOutcome]
    broken_writes: list[Exception] = field(default_factory=list)
    ended: bool = False
    started_at: float = field(init=False)
    driver: asyncio.Task[None] = field(init=False)
    deadline: TimerHandle = field(init=False)


class Game:
    """Runs one quiz per room: its runs one after another, each run's frames to the room's seats in order."""

    def __init__(self, rooms: CallRooms, scheduler: Scheduler, game_master: GameMaster) -> None:
        self._rooms = rooms
        self._scheduler = scheduler
        self._game_master = game_master
        self._quizzes: dict[str, Quiz] = {}
        self._runs: dict[str, list[_Run]] = {}
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
            case GameSignal.GAME_ANSWER:
                await self._answer(peer_id, frame.data)
            case GameSignal.GAME_END:
                await self._end(peer_id)
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

    async def _end(self, peer_id: PeerId) -> None:
        """End the room's quiz for everyone; it runs in the ender's receive loop, which never waits on a driver."""
        room_id = self._rooms.room_of(peer_id)
        quiz = None if room_id is None else self._quizzes.get(room_id)
        if room_id is None or quiz is None or quiz.is_over:
            return
        if not quiz.may_end(peer_id, hosts_call=self._rooms.is_host(peer_id)):
            await self._rooms.send(peer_id, _refusal_frame(Refused(GameRefusal.NOT_STARTER_OR_HOST)))
            return
        await self._end_for_everyone(room_id, quiz, ENDED_FRAME)

    async def _end_for_everyone(self, room_id: str, quiz: Quiz, notice: JsonObject) -> None:
        """Send the room's seats the notice, then idle, and remove the quiz; the quiz is not over yet."""
        ended = quiz.ended()
        self._quizzes[room_id] = ended
        self._phases.pop(room_id, None)
        for run in self._runs.get(room_id, []):
            # The room keeps the run until its call has closed, so a new quiz never overlaps it.
            run.ended = True
            run.deadline.cancel()
            run.ask.cancel()
        recipients = self._rooms.members(room_id)
        try:
            for frame in (notice, IDLE_FRAME):
                broken_writes = await self._send_isolated(
                    recipients, frame, lambda: self._quizzes.get(room_id) is ended
                )
                for error in broken_writes:
                    # Only the type is logged, never a name or an answer.
                    logger.error("game_end_failed", error=type(error).__name__)
        finally:
            if self._quizzes.get(room_id) is ended:
                del self._quizzes[room_id]

    async def _answer(self, peer_id: PeerId, data: JsonObject) -> None:
        room_id = self._rooms.room_of(peer_id)
        if room_id is None:
            return
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise InvalidMessageError("An answer needs text")
        quiz = self._quizzes.get(room_id)
        if quiz is None or not quiz.may_answer(peer_id):
            await self._rooms.send(peer_id, _refusal_frame(Refused(GameRefusal.ANSWER_CLOSED)))
            return
        if len(text.strip()) > GAME_ANSWER_MAX_CHARACTERS:
            await self._rooms.send(peer_id, _refusal_frame(Refused(GameRefusal.ANSWER_TOO_LONG)))
            return
        answered = quiz.with_answer(peer_id, self._rooms.name_of(peer_id), text.strip())
        self._quizzes[room_id] = answered
        await self._count_and_reveal_when_due(room_id, answered)

    async def _count_and_reveal_when_due(self, room_id: str, quiz: Quiz) -> None:
        present = self._present_players(room_id, quiz)
        await self._send_answer_count(room_id, quiz, present)
        if self._reveal_is_due(room_id, quiz, present):
            self._run(room_id, quiz.revealing())

    def _present_players(self, room_id: str, quiz: Quiz) -> frozenset[PeerId]:
        """The players still in the room; a parked seat stays in it until its grace ends."""
        return frozenset(player for player in quiz.players if self._rooms.room_of(player) == room_id)

    async def _send_answer_count(self, room_id: str, quiz: Quiz, present: frozenset[PeerId]) -> None:
        frame = _answer_count_frame(quiz, present)
        broken_writes = await self._send_isolated(
            self._rooms.members(room_id), frame, lambda: self._quizzes.get(room_id) is quiz
        )
        for error in broken_writes:
            # The sender's connection and the reveal check go on; only the type is logged, never the text.
            logger.error("game_answer_count_failed", error=type(error).__name__)

    def _reveal_is_due(self, room_id: str, quiz: Quiz, present: frozenset[PeerId]) -> bool:
        return (
            self._quizzes.get(room_id) is quiz
            and quiz.everyone_answered(present)
            and self._rooms.room_of(quiz.starter) == room_id
        )

    def _run(self, room_id: str, quiz: Quiz) -> None:
        # The quiz, the run, its tasks and its deadline exist before any await, so a second tap already finds them.
        # A reveal can arrive while the question run still delivers answering; it then waits for that run's end.
        starter = self._rooms.connection_of(quiz.starter)
        runs = self._runs.setdefault(room_id, [])
        previous = runs[-1].driver if runs else None
        run = _Run(
            quiz=quiz,
            previous=previous,
            ask=asyncio.create_task(self._ask_after(previous, starter, quiz.transcript)),
        )
        run.driver = asyncio.create_task(self._drive(room_id, run))
        run.deadline = self._scheduler.call_later(GAME_RUN_DEADLINE_SECONDS, partial(_expire, run))
        self._quizzes[room_id] = quiz
        runs.append(run)
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

    async def seat_left(self, peer_id: PeerId, *, room_id: str, name: str) -> None:
        """The starter's leave ends the quiz for everyone; a player's leave shrinks the count of an answered question.

        An ending that came first has left no quiz, or an over one, and the notice then sends nothing.
        """
        quiz = self._quizzes.get(room_id)
        if quiz is None or quiz.is_over:
            return
        if peer_id == quiz.starter:
            await self._end_for_everyone(room_id, quiz, {**ENDED_FRAME, "leaver": name})
        elif quiz.counts_leave_of(peer_id):
            # Replacing the quiz first makes a count or a reveal check still in flight find its quiz replaced.
            shrunk = quiz.without_player(peer_id)
            self._quizzes[room_id] = shrunk
            await self._count_and_reveal_when_due(room_id, shrunk)

    def room_ended(self, room_id: str) -> None:
        """Forget the room's quiz and runs; each run's driver finishes on its own and sends nothing more."""
        self._quizzes.pop(room_id, None)
        self._phases.pop(room_id, None)
        for run in self._runs.pop(room_id, []):
            run.deadline.cancel()
            run.ask.cancel()

    async def shut_down(self) -> None:
        # A quiz waiting for its reveal has no run, and a run delivering idle has no quiz any more.
        for room_id in {*self._quizzes, *self._runs}:
            self.room_ended(room_id)
        if self._drivers:
            await asyncio.wait(self._drivers)

    async def _drive(self, room_id: str, run: _Run) -> None:
        # Never cancelled, so no socket write is cut short; the room's end only cancels `ask`. A seat's
        # broken write is held until the run is delivered, so the room keeps the run until `ask` has ended.
        try:
            if run.previous is not None:
                await asyncio.wait({run.previous})
            run.started_at = self._scheduler.now()
            await self._announce_thinking(room_id, run)
            await asyncio.wait({run.ask})
            outcome = _outcome_of(run.ask)
            run.deadline.cancel()
            self._log_run(room_id, run, outcome)
            await self._deliver(room_id, run, outcome)
        finally:
            if self._holds(room_id, run):
                self._forget(room_id, run)
        if run.broken_writes:
            raise run.broken_writes[0]

    async def _deliver(self, room_id: str, run: _Run, outcome: GameOutcome) -> None:
        if not self._is_live(room_id, run):
            return
        if isinstance(outcome, Line):
            recipients = self._rooms.members(room_id)
            await self._send_each(room_id, run, recipients, _line_frame(outcome, run.quiz))
            quiz = run.quiz.heard(outcome.text, frozenset(PeerId(seat) for seat in recipients))
        else:
            # The refusal ends the quiz: it is over before the first write, so no End starts a second ending.
            self._quizzes[room_id] = run.quiz.ended()
            seated = self._rooms.members(room_id)
            await self._send_each(room_id, run, [run.quiz.starter], _refusal_frame(outcome))
            if not run.quiz.is_opening:
                others = [seat for seat in seated if seat != run.quiz.starter]
                await self._send_each(room_id, run, others, ENDED_FRAME)
            quiz = None
        if not self._is_live(room_id, run):
            return
        # Each phase's recipients are read in the same step that sets it, so every seat seated now is told
        # here, a seat that joins later is greeted by `seat_joined`, and idle greets no one.
        if quiz is None or quiz.is_over:
            await self._end_quiz(room_id, run)
        elif quiz.awaits_game_master:
            self._chain(room_id, quiz)
        else:
            await self._open_question(room_id, run, quiz)

    def _chain(self, room_id: str, quiz: Quiz) -> None:
        """Run the quiz's next request at once, on the starter's token and with no idle in between.

        A starter whose seat is gone gets no run: that seat's `seat_left` notice ends the quiz.
        """
        if self._rooms.room_of(quiz.starter) is not None:
            self._run(room_id, quiz)

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

    async def _ask_after(
        self, previous: asyncio.Task[None] | None, starter: HTTPConnection, transcript: tuple[Turn, ...]
    ) -> GameOutcome:
        if previous is not None:
            await asyncio.wait({previous})
        return await self._game_master.ask(starter, transcript)

    def _holds(self, room_id: str, run: _Run) -> bool:
        return run in self._runs.get(room_id, [])

    def _is_live(self, room_id: str, run: _Run) -> bool:
        return not run.ended and self._holds(room_id, run)

    def _forget(self, room_id: str, run: _Run) -> None:
        runs = self._runs[room_id]
        runs.remove(run)
        if not runs:
            del self._runs[room_id]

    async def _announce_thinking(self, room_id: str, run: _Run) -> None:
        # A seat greeted while the run was starting already holds its thinking frame; each seat gets one.
        if not self._is_live(room_id, run):
            return
        phase = self._phases[room_id]
        recipients = [peer_id for peer_id in self._rooms.members(room_id) if peer_id not in phase.told]
        phase.told.update(recipients)
        await self._send_each(room_id, run, recipients, phase.frame)

    async def _send_to_room(self, room_id: str, run: _Run, frame: JsonObject) -> None:
        # Each caller has checked, in this same step, that the run is still the room's, so the room exists.
        await self._send_each(room_id, run, self._rooms.members(room_id), frame)

    async def _send_each(self, room_id: str, run: _Run, recipients: list[str], frame: JsonObject) -> None:
        # Re-raised by the driver once every other seat has its frames.
        run.broken_writes.extend(await self._send_isolated(recipients, frame, lambda: self._is_live(room_id, run)))

    async def _send_isolated(
        self, recipients: list[str], frame: JsonObject, still_wanted: Callable[[], bool]
    ) -> list[Exception]:
        """Send the frame to each recipient while it is wanted; a seat's broken write never keeps the others from it."""
        broken_writes: list[Exception] = []
        for peer_id in recipients:
            if not still_wanted():
                break
            try:
                await self._rooms.send(peer_id, frame)
            except Exception as error:
                broken_writes.append(error)
        return broken_writes

    def _log_run(self, room_id: str, run: _Run, outcome: GameOutcome) -> None:
        shown = "line" if isinstance(outcome, Line) else outcome.reason.value
        if run.ended:
            shown = "ended"
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
