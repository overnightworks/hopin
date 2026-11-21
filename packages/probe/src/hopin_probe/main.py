# ruff: noqa: T201 - the probe's one output owner: each line it prints is a verdict or a failed stage, only codes and counts
"""The `hopin-probe` command: signs in, starts a call and prints one line per check.

Exit 0 when every check says yes, 1 when one says no, 2 when the probe could not run its checks. Only codes and counts
leave the process, on stdout and stderr alike: no text, no name, no room, no link, no credential, no exception message.
"""

import sys
from typing import TYPE_CHECKING

from hopin_probe.checks import Limits, Stage, StartFailure, Verdict, not_reached, run_checks
from hopin_probe.page import AgentSeat, open_origin, sign_in, signed_out_page
from hopin_probe.settings import load_settings
from hopin_probe.voice import question_for, synthesise

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

ALL_YES = 0
SOME_NO = 1
NOT_RUN = 2


def _say(line: str) -> None:
    print(line, flush=True)


def _report_uncaught(kind: type[BaseException], _value: BaseException, _traceback: TracebackType | None) -> None:
    _say(f"verdict=failed reason=error error={kind.__name__}")


def _run(launch_arguments: Sequence[str]) -> list[Verdict]:
    """The checks' verdicts, or the stage that could not finish as a `StartFailure`."""
    stage = Stage.SETTINGS
    try:
        settings = load_settings()
        password = settings.read_password()
        stage = Stage.VOICE
        agent = settings.agents[0]
        clip = synthesise(settings.voice_file, question_for(agent))
        stage = Stage.OPEN
        with signed_out_page([clip], launch_arguments) as page:
            open_origin(page, settings.base_url)
            stage = Stage.SIGN_IN
            sign_in(page, settings.base_url, settings.username, password)
            stage = Stage.CALL
            seat = AgentSeat(page, settings.base_url, agent)
            seat.start_call()
            return run_checks(seat, Limits(settings.join_seconds, settings.answer_seconds))
    except StartFailure:
        raise
    except Exception as error:  # only the class is reported; a message may hold what a page expected
        raise StartFailure(stage, "error", error=type(error).__name__) from None


def main(launch_arguments: Sequence[str] = ()) -> int:
    sys.excepthook = _report_uncaught
    try:
        verdicts = _run(launch_arguments)
    except StartFailure as failure:
        _say(failure.line())
        verdicts = not_reached()
        exit_code = NOT_RUN
    else:
        exit_code = ALL_YES if all(verdict.yes for verdict in verdicts) else SOME_NO
    for verdict in verdicts:
        _say(verdict.line())
    return exit_code
