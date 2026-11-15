import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

import structlog.testing

if TYPE_CHECKING:
    from collections.abc import Iterator

HOPIN_LOGGER_NAME = "hopin"
_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class _StepLines(logging.Handler):
    """Collects the standard-library records of hopin's step lines in the shape structlog's capture gives its own."""

    def __init__(self, lines: list[dict]) -> None:
        super().__init__(logging.NOTSET)
        self._lines = lines

    def emit(self, record: logging.LogRecord) -> None:
        fields = {name: value for name, value in vars(record).items() if name not in _RECORD_ATTRIBUTES}
        self._lines.append({"event": record.getMessage(), "log_level": record.levelname.lower(), **fields})


@contextmanager
def captured_lines() -> Iterator[list[dict]]:
    """Every line hopin writes while the block runs, structlog's and the step timer's, each as its event, its level
    and its fields."""
    hopin_logger = logging.getLogger(HOPIN_LOGGER_NAME)
    previous_level = hopin_logger.level
    with structlog.testing.capture_logs() as lines:
        handler = _StepLines(lines)
        hopin_logger.addHandler(handler)
        hopin_logger.setLevel(logging.DEBUG)
        try:
            yield lines
        finally:
            hopin_logger.setLevel(previous_level)
            hopin_logger.removeHandler(handler)
