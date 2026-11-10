import logging
from functools import partial
from typing import TYPE_CHECKING

import pytest

# Assertion rewriting must be registered before the first import of a support module.
# The `tests` namespace itself is already imported when this conftest loads.
pytest.register_assert_rewrite("tests.support", "tests.signaling.support", "tests.e2e.support")
pytest.register_assert_rewrite("tests.game.support")  # game

from hopin.rooms.manager import RoomManager  # noqa: E402
from hopin.signaling.handler import SignalingHandler  # noqa: E402
from tests.support.dialed_call import serving_dialed_call  # noqa: E402
from tests.support.fake_issuer import FakeIssuer  # noqa: E402
from tests.support.page_build import FIXTURE_DIRECTORY  # noqa: E402
from tests.support.sign_in_settings import SignInSettings, build_sign_in_settings  # noqa: E402
from tests.support.signaling_fakes import ROOM_LIMIT, ManualScheduler, SocketAccountResolver  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def sign_in_settings(tmp_path: Path) -> Callable[..., SignInSettings]:
    return partial(build_sign_in_settings, tmp_path)


@pytest.fixture
def fixture_build_directory(monkeypatch: pytest.MonkeyPatch) -> Path:
    """`hopin.main` loads the committed fixture build, because the unit job has no Node and no build."""
    monkeypatch.setattr("hopin.main.BUILD_DIRECTORY", FIXTURE_DIRECTORY)
    monkeypatch.setenv("HOPIN_AGENT__BACKEND", "litellm")  # game
    monkeypatch.setenv("HOPIN_AGENT__GATEWAY__ORIGIN", "http://gateway.test:4000")  # game
    monkeypatch.setenv("HOPIN_AGENT__GATEWAY__KEY_FILE", "/nonexistent/gateway-key")  # game
    monkeypatch.setenv("HOPIN_AGENT__API_GROUP", "/services/scenario/api")  # game
    return FIXTURE_DIRECTORY


@pytest.fixture
def room_manager():
    return RoomManager(max_participants_per_room=ROOM_LIMIT)


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def handler(room_manager, scheduler):
    return SignalingHandler(room_manager, SocketAccountResolver(), scheduler)


@pytest.fixture
def dialed_call(sign_in_settings: Callable[..., SignInSettings]):
    """HopIn with participation and a fake issuer, and the means to seat people and call its MCP endpoint."""
    sign_in = sign_in_settings()
    with serving_dialed_call(sign_in, FakeIssuer(sign_in)) as call:
        yield call


class _RenderedRecords(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.rendered: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.rendered.append(logging.Formatter().format(record))


@pytest.fixture
def urllib3_records() -> Iterator[list[str]]:
    """Every record urllib3's loggers let through while the test runs, at any level, as rendered."""
    handler = _RenderedRecords()
    urllib3_logger = logging.getLogger("urllib3")
    previous_level = urllib3_logger.level
    logging.getLogger().addHandler(handler)
    urllib3_logger.setLevel(logging.DEBUG)
    yield handler.rendered
    urllib3_logger.setLevel(previous_level)
    logging.getLogger().removeHandler(handler)
