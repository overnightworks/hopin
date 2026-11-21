import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from hopin_probe.checks import JOINED, Limits, SwitchState, run_checks
from tests.probe.conftest import NOT_REACHED, SENTINEL

if TYPE_CHECKING:
    from collections.abc import Callable

LIMITS = Limits(join_seconds=1, answer_seconds=1)


@dataclass
class ScriptedSeat:
    """A call page that answers what a test scripts."""

    offered: bool = True
    outcome_shown: str | None = JOINED
    switch: SwitchState = SwitchState.ON
    speaks: bool = True
    fails_reading_the_switch_with: Exception | None = None

    def offer_appears(self, seconds: int) -> bool:
        return self.offered

    def call_agent(self) -> None:
        pass

    def outcome(self, seconds: int) -> str | None:
        return self.outcome_shown

    def hearing_switch(self) -> SwitchState:
        if self.fails_reading_the_switch_with is not None:
            raise self.fails_reading_the_switch_with
        return self.switch

    def say_question(self) -> None:
        pass

    def agent_is_speaking(self, seconds: int) -> bool:
        return self.speaks


def lines_of(seat: ScriptedSeat) -> list[str]:
    return [verdict.line() for verdict in run_checks(seat, LIMITS)]


def test_an_agent_that_joins_has_a_switch_on_and_speaks_gets_three_yes_lines() -> None:
    assert [re.sub(r"seconds=\d+", "seconds=N", line) for line in lines_of(ScriptedSeat())] == [
        "check=A verdict=yes seconds=N",
        "check=B verdict=yes",
        "check=C verdict=yes seconds=N",
    ]


@pytest.mark.parametrize(
    ("seat", "reason"),
    [
        pytest.param(ScriptedSeat(offered=False), "no_row", id="not-offered"),
        pytest.param(ScriptedSeat(outcome_shown=None), "no_answer", id="never-answers"),
        pytest.param(ScriptedSeat(outcome_shown="busy"), "busy", id="refuses-as-busy"),
    ],
)
def test_an_agent_that_does_not_join_is_a_no_on_a_and_leaves_the_other_checks_not_reached(
    seat: ScriptedSeat, reason: str
) -> None:
    assert lines_of(seat) == [f"check=A verdict=no reason={reason}", *NOT_REACHED[1:]]


@pytest.mark.parametrize(
    ("switch", "reason"),
    [pytest.param(SwitchState.OFF, "off", id="off"), pytest.param(SwitchState.MISSING, "missing", id="missing")],
)
def test_a_hearing_switch_that_is_not_on_is_a_no_on_b_and_c_is_still_read(switch: SwitchState, reason: str) -> None:
    _, second, third = lines_of(ScriptedSeat(switch=switch))

    assert second == f"check=B verdict=no reason={reason}"
    assert third.startswith("check=C verdict=yes")


def test_an_agent_that_does_not_speak_is_a_no_on_c() -> None:
    assert lines_of(ScriptedSeat(speaks=False))[2] == "check=C verdict=no reason=none"


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(lambda: PlaywrightTimeoutError(f"waiting for {SENTINEL}"), id="timeout"),
        pytest.param(lambda: RuntimeError(SENTINEL), id="other"),
    ],
)
def test_an_exception_in_a_check_prints_its_class_and_never_its_message(failure: Callable[[], Exception]) -> None:
    exception = failure()

    lines = lines_of(ScriptedSeat(fails_reading_the_switch_with=exception))

    assert lines[1] == f"check=B verdict=no reason=error error={type(exception).__name__}"
    assert all(SENTINEL not in line for line in lines)
