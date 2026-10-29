import logging
from functools import partial

import pytest
import pytest_asyncio

from hopin.game.table import Game
from hopin.signaling.handler import SignalingHandler
from tests.game.support.fake_game_master import FakeGameMaster
from tests.game.support.table_world import fixed_topics, shut_down_within_cap
from tests.signaling.support.browsers import Link
from tests.support.signaling_fakes import SocketAccountResolver

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
    return partial(Game, scheduler=scheduler, game_master=game_master, topic_drawer=fixed_topics)


@pytest.fixture
def handler(room_manager, scheduler, game):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler, game)


@pytest_asyncio.fixture
async def link(handler, game_master):
    link = Link(handler)
    yield link
    await link.close_all()
    game_master.release_every_hold()
    await shut_down_within_cap(handler)
