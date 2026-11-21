"""What the probe reports: a record per check and per failed stage, and the checks themselves, read through a seat."""

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

JOINED = "joined"


class Check(StrEnum):
    AGENT_JOINS = "A"
    AGENT_MAY_HEAR = "B"
    AGENT_ANSWERS = "C"


class Stage(StrEnum):
    SETTINGS = "settings"
    VOICE = "voice"
    OPEN = "open"
    SIGN_IN = "sign_in"
    CALL = "call"


class SwitchState(StrEnum):
    """The hearing switch on the agent's tile, as its `aria-checked` says; absent when the tile has none."""

    ON = "true"
    OFF = "false"
    MISSING = "missing"


class StartFailure(Exception):  # noqa: N818 - the name is the stage line's word
    """A stage that could not finish. Only its codes are kept, never a message."""

    def __init__(self, stage: Stage, reason: str, *, field: str | None = None, error: str | None = None) -> None:
        super().__init__(stage, reason)
        self.stage = stage
        self.reason = reason
        self.field = field
        self.error = error

    def line(self) -> str:
        parts = [f"stage={self.stage}", "verdict=failed", f"reason={self.reason}"]
        parts += [f"{name}={value}" for name, value in (("field", self.field), ("error", self.error)) if value]
        return " ".join(parts)


@dataclass(frozen=True)
class Verdict:
    check: Check
    yes: bool
    reason: str | None = None
    seconds: int | None = None
    error: str | None = None

    def line(self) -> str:
        parts = [f"check={self.check}", f"verdict={'yes' if self.yes else 'no'}"]
        fields = (("reason", self.reason), ("seconds", self.seconds), ("error", self.error))
        return " ".join(parts + [f"{name}={value}" for name, value in fields if value is not None])


def not_reached() -> list[Verdict]:
    return [Verdict(check, yes=False, reason="not_reached") for check in Check]


class Seat(Protocol):
    """The call page as the checks read it."""

    def offer_appears(self, seconds: int) -> bool: ...

    def call_agent(self) -> None: ...

    def outcome(self, seconds: int) -> str | None:
        """`JOINED`, the code of the refusal the agent's row shows, or None when neither came in time."""
        ...

    def hearing_switch(self) -> SwitchState: ...

    def say_question(self) -> None: ...

    def agent_is_speaking(self, seconds: int) -> bool: ...


@dataclass(frozen=True)
class Limits:
    join_seconds: int
    answer_seconds: int


def _seconds_since(started: float) -> int:
    return round(time.monotonic() - started)


def agent_joins(seat: Seat, limits: Limits) -> Verdict:
    started = time.monotonic()
    if not seat.offer_appears(limits.join_seconds):
        return Verdict(Check.AGENT_JOINS, yes=False, reason="no_row")
    seat.call_agent()
    outcome = seat.outcome(limits.join_seconds) or "no_answer"
    if outcome != JOINED:
        return Verdict(Check.AGENT_JOINS, yes=False, reason=outcome)
    return Verdict(Check.AGENT_JOINS, yes=True, seconds=_seconds_since(started))


_SWITCH_REASONS = {SwitchState.ON: None, SwitchState.OFF: "off", SwitchState.MISSING: "missing"}


def agent_may_hear(seat: Seat, _limits: Limits) -> Verdict:
    state = seat.hearing_switch()
    return Verdict(Check.AGENT_MAY_HEAR, yes=state is SwitchState.ON, reason=_SWITCH_REASONS[state])


def agent_answers(seat: Seat, limits: Limits) -> Verdict:
    seat.say_question()
    started = time.monotonic()
    if not seat.agent_is_speaking(limits.answer_seconds):
        return Verdict(Check.AGENT_ANSWERS, yes=False, reason="none")
    return Verdict(Check.AGENT_ANSWERS, yes=True, seconds=_seconds_since(started))


def _run(check: Check, step: Callable[[Seat, Limits], Verdict], seat: Seat, limits: Limits) -> Verdict:
    try:
        return step(seat, limits)
    except Exception as error:  # the verdict names the class; the message may hold the text a page expected
        return Verdict(check, yes=False, reason="error", error=type(error).__name__)


def run_checks(seat: Seat, limits: Limits) -> list[Verdict]:
    """A, B and C in order; a no on A leaves the other two not reached, since they need the agent in the call."""
    joined = _run(Check.AGENT_JOINS, agent_joins, seat, limits)
    if not joined.yes:
        return [joined, *not_reached()[1:]]
    return [
        joined,
        _run(Check.AGENT_MAY_HEAR, agent_may_hear, seat, limits),
        _run(Check.AGENT_ANSWERS, agent_answers, seat, limits),
    ]
