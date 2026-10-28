import logging
from functools import partial

import pytest

# Assertion rewriting must be registered before the first import of a support module.
pytest.register_assert_rewrite("tests.game.support")

from hopin.game.table import Game  # noqa: E402
from hopin.signaling.handler import SignalingHandler  # noqa: E402
from tests.game.support.fake_game_master import FakeGameMaster  # noqa: E402
from tests.support.signaling_fakes import SocketAccountResolver  # noqa: E402

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


@pytest.fixture
def game_master():
    return FakeGameMaster()


@pytest.fixture
def game(scheduler, game_master):
    """The game as `create_app` builds it, its game master a fake."""
    return partial(Game, scheduler=scheduler, game_master=game_master)


@pytest.fixture
def handler(room_manager, scheduler, game):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler, game)
