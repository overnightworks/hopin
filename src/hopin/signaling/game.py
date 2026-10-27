import asyncio
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final, Protocol

import structlog

from hopin.config.constants import (
    GAME_HISTORY_LIMIT,
    GAME_RUN_DEADLINE_SECONDS,
    GamePhase,
    GameRefusal,
    SignalType,
)
from hopin.signaling.frames import TYPE_KEY, JsonObject, PeerId

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from hopin.rooms.manager import RoomManager
    from hopin.signaling.routing import RoomRouting
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

MILLISECONDS_PER_SECOND: Final = 1000


@dataclass(frozen=True)
class Question:
    text: str


@dataclass(frozen=True)
class Refused:
    reason: GameRefusal
    retry_after_seconds: int | None = None


type GameOutcome = Question | Refused


class GameMaster(Protocol):
    async def ask(self, starter: WebSocket, questions: tuple[str, ...]) -> GameOutcome:
        """The next question on the starter's access, after `questions`, or why there is none.

        Raises nothing but `CancelledError`; a cancelled call has closed its connection when it ends.
        """


def _phase_frame(phase: GamePhase) -> JsonObject:
    return {TYPE_KEY: SignalType.GAME_PHASE.value, "phase": phase.value}


def _refusal_frame(refusal: Refused) -> JsonObject:
    frame: JsonObject = {TYPE_KEY: SignalType.GAME_REFUSED.value, "reason": refusal.reason.value}
    if refusal.retry_after_seconds is not None:
        frame["retry_after_seconds"] = refusal.retry_after_seconds
    return frame


@dataclass(eq=False)
class _Run:
    """A room's one run in flight; the record itself is the run's generation."""

    starter: PeerId
    started_at: float
    ask: asyncio.Task[GameOutcome]
    phase: GamePhase = GamePhase.THINKING
    told_thinking: set[str] = field(default_factory=set)
    driver: asyncio.Task[None] = field(init=False)
    deadline: TimerHandle = field(init=False)


class Game:
    """Runs the game master for each room: at most one run in flight, its frames to the room's seats in order."""

    def __init__(
        self, routing: RoomRouting, room_manager: RoomManager, scheduler: Scheduler, game_master: GameMaster
    ) -> None:
        self._routing = routing
        self._seats = routing.seats
        self._room_manager = room_manager
        self._scheduler = scheduler
        self._game_master = game_master
        self._runs: dict[str, _Run] = {}
        self._questions: dict[str, deque[str]] = {}
        self._drivers: set[asyncio.Task[None]] = set()

    async def start(self, peer_id: PeerId) -> None:
        room_id = self._routing.room_of(peer_id)
        if room_id is None:
            return
        if room_id in self._runs:
            await self._seats.send(peer_id, _refusal_frame(Refused(GameRefusal.ALREADY_ANSWERING)))
            return
        # The record, its tasks and its deadline exist before any await, so a second start already finds it.
        questions = tuple(self._questions.get(room_id, ()))
        starter_socket = self._seats.seat(peer_id).websocket
        run = _Run(
            starter=peer_id,
            started_at=self._scheduler.now(),
            ask=asyncio.create_task(self._game_master.ask(starter_socket, questions)),
        )
        run.driver = asyncio.create_task(self._drive(room_id, run))
        run.deadline = self._scheduler.call_later(GAME_RUN_DEADLINE_SECONDS, partial(_expire, run))
        self._runs[room_id] = run
        self._drivers.add(run.driver)
        run.driver.add_done_callback(self._driver_finished)

    async def greet(self, peer_id: PeerId) -> None:
        """Tell a seat that just joined that its room's game master is thinking."""
        room_id = self._routing.room_of(peer_id)
        run = None if room_id is None else self._runs.get(room_id)
        if run is not None and run.phase is GamePhase.THINKING and peer_id not in run.told_thinking:
            run.told_thinking.add(peer_id)
            await self._seats.send(peer_id, _phase_frame(GamePhase.THINKING))

    def room_ended(self, room_id: str) -> None:
        """Forget the room's run and questions; the run's driver finishes on its own and sends nothing more."""
        self._questions.pop(room_id, None)
        run = self._runs.pop(room_id, None)
        if run is None:
            return
        run.deadline.cancel()
        run.ask.cancel()

    async def shut_down(self) -> None:
        for room_id in list(self._runs):
            self.room_ended(room_id)
        if self._drivers:
            await asyncio.wait(self._drivers)

    async def _drive(self, room_id: str, run: _Run) -> None:
        # Never cancelled, so no socket write is cut short; the room's end only cancels `ask`.
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

    async def _deliver(self, room_id: str, run: _Run, outcome: GameOutcome) -> None:
        if self._runs.get(room_id) is not run:
            return
        if isinstance(outcome, Question):
            self._questions.setdefault(room_id, deque(maxlen=GAME_HISTORY_LIMIT)).append(outcome.text)
            await self._send_to_room(room_id, run, {TYPE_KEY: SignalType.GAME_LINE.value, "text": outcome.text})
        else:
            await self._seats.send(run.starter, _refusal_frame(outcome))
        if self._runs.get(room_id) is not run:
            return
        # Idle's recipients are read in the same step that turns the phase idle, so a seat that joins
        # later is greeted with no phase, and every seat that saw thinking also sees idle.
        run.phase = GamePhase.IDLE
        await self._send_to_room(room_id, run, _phase_frame(GamePhase.IDLE))

    async def _announce_thinking(self, room_id: str, run: _Run) -> None:
        # A seat greeted while the run was starting already holds its thinking frame; each seat gets one.
        if self._runs.get(room_id) is not run:
            return
        seated = self._room_manager.get_room(room_id).participant_ids
        recipients = [peer_id for peer_id in seated if peer_id not in run.told_thinking]
        run.told_thinking.update(recipients)
        await self._send_each(room_id, run, recipients, _phase_frame(GamePhase.THINKING))

    async def _send_to_room(self, room_id: str, run: _Run, frame: JsonObject) -> None:
        if self._runs.get(room_id) is not run:
            return
        await self._send_each(room_id, run, self._room_manager.get_room(room_id).participant_ids, frame)

    async def _send_each(self, room_id: str, run: _Run, recipients: list[str], frame: JsonObject) -> None:
        for peer_id in recipients:
            if self._runs.get(room_id) is not run:
                return
            await self._seats.send(peer_id, frame)

    def _log_run(self, room_id: str, run: _Run, outcome: GameOutcome) -> None:
        shown = "question" if isinstance(outcome, Question) else outcome.reason.value
        duration_ms = round((self._scheduler.now() - run.started_at) * MILLISECONDS_PER_SECOND)
        logger.info("game_master_run", room_id=room_id, outcome=shown, duration_ms=duration_ms)

    def _driver_finished(self, driver: asyncio.Task[None]) -> None:
        self._drivers.discard(driver)
        if not driver.cancelled() and (error := driver.exception()) is not None:
            logger.error("game_driver_failed", error=type(error).__name__)


async def _expire(run: _Run) -> None:
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
