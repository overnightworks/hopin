import logging
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

import structlog
import structlog.testing

if TYPE_CHECKING:
    from collections.abc import Iterator

HOPIN_LOGGER_NAME = "hopin"
_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class Lines(list):
    """The lines a block wrote, each as its event, its level and its fields; a line written on another thread joins
    the list as it arrives."""

    def __init__(self) -> None:
        super().__init__()
        self._arrival = threading.Condition()

    def add(self, line: dict) -> None:
        with self._arrival:
            self.append(line)
            self._arrival.notify_all()

    def wait_for_event(self, event: str, seconds: float) -> bool:
        """Whether a line of the event arrived within the seconds."""
        with self._arrival:
            return self._arrival.wait_for(lambda: any(line["event"] == event for line in self), seconds)


class _StepLines(logging.Handler):
    """Collects the standard-library records of hopin's step lines."""

    def __init__(self, lines: Lines) -> None:
        super().__init__(logging.NOTSET)
        self._lines = lines

    def emit(self, record: logging.LogRecord) -> None:
        fields = {name: value for name, value in vars(record).items() if name not in _RECORD_ATTRIBUTES}
        self._lines.add({"event": record.getMessage(), "log_level": record.levelname.lower(), **fields})


@contextmanager
def captured_lines() -> Iterator[Lines]:
    """Every line hopin writes while the block runs, structlog's and the step timer's."""
    lines = Lines()

    def collect(_logger: object, method_name: str, event_dict: dict) -> dict:
        lines.add({**event_dict, "log_level": method_name})
        raise structlog.DropEvent

    hopin_logger = logging.getLogger(HOPIN_LOGGER_NAME)
    previous_level = hopin_logger.level
    handler = _StepLines(lines)
    with structlog.testing.capture_logs(processors=[collect]):
        hopin_logger.addHandler(handler)
        hopin_logger.setLevel(logging.DEBUG)
        try:
            yield lines
        finally:
            hopin_logger.setLevel(previous_level)
            hopin_logger.removeHandler(handler)


def runs_logged(lines: list[dict]) -> list[str]:
    """How each game master run ended, as its line says: the reason of a run that showed no line, else its outcome."""
    return [line.get("reason", line["outcome"]) for line in lines if line["event"] == "game_master_run.ended"]


def ended_steps(lines: list[dict], event: str) -> list[tuple[str, str, str | None]]:
    """The level, the outcome and the reason of each step line of the event, in order."""
    return [(line["log_level"], line["outcome"], line.get("reason")) for line in lines if line["event"] == event]
