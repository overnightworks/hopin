import logging
import socket
from functools import partial
from typing import TYPE_CHECKING

import pytest
from fakeredis import FakeServer
from logline.testing import configured

# Assertion rewriting must be registered before the first import of a support module.
# The `tests` namespace itself is already imported when this conftest loads.
pytest.register_assert_rewrite("tests.support", "tests.signaling.support", "tests.e2e.support")
pytest.register_assert_rewrite("tests.game.support")  # game

from hopin.config.settings import RealmSettings, ServerSettings  # noqa: E402
from hopin.main import build_application, main, run_uvicorn, uvicorn_options  # noqa: E402
from hopin.signaling.handler import SignalingHandler  # noqa: E402
from hopin.signaling.scheduler import InMemoryDeadlines  # noqa: E402
from tests.stores.support.postgres_identity import empty_database, postgres_stores, postgres_stores_with  # noqa: E402, F401
from tests.stores.support.real_redis import FAKEREDIS, redis_url  # noqa: E402, F401
from tests.stores.support.served_pods import (  # noqa: E402, F401
    migrated_database,
    pods,
    redis_gate,
    served_environment,
    web_auth_config,
)
from tests.support.call_state import call_store_over  # noqa: E402
from tests.support.dialed_call import serving_dialed_call  # noqa: E402
from tests.support.fake_issuer import FakeIssuer  # noqa: E402
from tests.support.game_environment import set_game_environment  # noqa: E402  # game
from tests.support.in_thread_stores import in_thread_stores  # noqa: E402
from tests.support.page_build import fixture_page_build  # noqa: E402
from tests.support.sign_in_settings import SignInSettings, build_sign_in_settings  # noqa: E402
from tests.support.signaling_fakes import ManualScheduler, SocketAccountResolver  # noqa: E402
from tests.support.stores import single_process_stores  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def sign_in_settings(tmp_path: Path) -> Callable[..., SignInSettings]:
    return partial(build_sign_in_settings, tmp_path)


@pytest.fixture
def logging_restored() -> Iterator[None]:
    """A test that runs `hopin.main.main` leaves the process's logging as it found it."""
    with configured("hopin"):
        yield


@pytest.fixture
def serve_refusal(
    sign_in_settings: Callable[..., SignInSettings], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Callable[..., None]:
    """Asserts `hopin serve` (or another command) refuses in one line naming the setting and never its value."""

    def assert_refused(setting: str, value: str | None, expected_line: str, command: str = "serve") -> None:
        for name, environment_value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, environment_value)
        if value is None:
            monkeypatch.delenv(setting)
        else:
            monkeypatch.setenv(setting, value)

        with pytest.raises(SystemExit) as refusal:
            main([command])

        assert refusal.value.code == 1
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 1
        assert expected_line in lines[0]
        assert "sentinel" not in lines[0]

    return assert_refused


@pytest.fixture
def game_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings environment a test needs when the game master is part of the build."""
    set_game_environment(monkeypatch)  # game


@pytest.fixture
def start_on_taken_port(sign_in_settings, monkeypatch: pytest.MonkeyPatch) -> Callable[[], SystemExit]:
    """Starts the server on a port another socket holds and returns the exit uvicorn refuses it with."""
    for name, value in sign_in_settings().environment.items():
        monkeypatch.setenv(name, value)

    def start() -> SystemExit:
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            monkeypatch.setenv("HOPIN_HOST", "127.0.0.1")
            monkeypatch.setenv("HOPIN_PORT", str(taken.getsockname()[1]))
            settings = ServerSettings()
            application = build_application(
                settings, RealmSettings(), fixture_page_build(), in_thread_stores(), single_process_stores()
            )
            with pytest.raises(SystemExit) as refusal:
                run_uvicorn(application, uvicorn_options(settings))
        return refusal.value

    return start


@pytest.fixture
def redis_server(request):
    """The Redis a test's processes share: a fakeredis server, or the url of the job's real Redis."""
    return FakeServer() if getattr(request, "param", FAKEREDIS) == FAKEREDIS else request.getfixturevalue("redis_url")


@pytest.fixture
def call_store(redis_server):
    """The call store of the test's one process."""
    return call_store_over(redis_server)


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def deadlines(scheduler):
    return InMemoryDeadlines(scheduler)


@pytest.fixture
def handler(call_store, scheduler, deadlines):
    return SignalingHandler(call_store, SocketAccountResolver(), scheduler, deadlines)


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
