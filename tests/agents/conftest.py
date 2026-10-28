import logging

import pytest

LOGGERS_THAT_MUST_STAY_SILENT = ("httpx2", "httpcore2", "urllib3", "webauth")


@pytest.fixture
def stdlib_records():
    """Every record the HTTP and sign-in libraries log at DEBUG and above, formatted as a handler would."""
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(logging.Formatter().format(record))

    handler = Keep(logging.DEBUG)
    loggers = [logging.getLogger(name) for name in LOGGERS_THAT_MUST_STAY_SILENT]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    yield records
    for logger, level in zip(loggers, levels, strict=True):
        logger.removeHandler(handler)
        logger.setLevel(level)
