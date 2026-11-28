import itertools
from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from redis import Redis

from hopin.auth.sign_in import build_sign_in_configs
from hopin.config.constants import HEALTH_PATH
from hopin.config.settings import ServerSettings
from hopin.main import main
from tests.stores.support.call_client import Browser
from tests.stores.support.pod_pair import serving_pod_pair, starting_pod
from tests.stores.support.service_gate import ServiceGate
from tests.support.in_thread_stores import in_thread_stores
from tests.support.sign_in_settings import TEST_REALM

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from webauth.config import WebAuthConfig

    from tests.stores.support.pod_pair import Pod
    from tests.stores.support.scratch_database import ScratchDatabase
    from tests.support.sign_in_settings import SignInSettings
    from tests.support.signed_in import SeededMember


@pytest.fixture
def redis_gate(redis_url: str) -> Iterator[ServiceGate]:
    gate = ServiceGate(redis_url)
    yield gate
    gate.close()


@pytest.fixture
def served_environment(
    sign_in_settings: Callable[..., SignInSettings],
    empty_database: ScratchDatabase,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
    game_environment: None,
    logging_restored: None,
) -> dict[str, str]:
    """The environment `hopin` reads, in this process, over the real Redis and the empty database."""
    environment = sign_in_settings(redis_url=redis_url, **empty_database.settings).environment
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return environment


@pytest.fixture
def migrated_database(empty_database: ScratchDatabase, served_environment: dict[str, str]) -> ScratchDatabase:
    """The database after `hopin migrate`."""
    main(["migrate"])
    return empty_database


@pytest.fixture
def pods(
    sign_in_settings: Callable[..., SignInSettings],
    redis_gate: ServiceGate,
    migrated_database: ScratchDatabase,
    tmp_path: Path,
) -> Iterator[tuple[Pod, Pod]]:
    environment = sign_in_settings(**migrated_database.settings).environment
    with serving_pod_pair(environment, redis_gate.url, tmp_path, HEALTH_PATH) as pair:
        yield pair


@pytest.fixture
def another_pod(
    sign_in_settings: Callable[..., SignInSettings],
    redis_gate: ServiceGate,
    migrated_database: ScratchDatabase,
    tmp_path: Path,
) -> Iterator[Callable[[], Pod]]:
    """Starts one more `hopin serve` beside whatever runs, reaching Redis through the gate, and returns it once it
    answers `/health`; every pod it started is stopped at the end."""
    environment = sign_in_settings(**migrated_database.settings).environment
    with ExitStack() as started:

        def start() -> Pod:
            pod = started.enter_context(
                starting_pod(environment, redis_gate.url, tmp_path, name=f"later-{next(count)}")
            )
            pod.wait_until_serving(HEALTH_PATH)
            return pod

        count = itertools.count(1)
        yield start


@pytest.fixture
def pod(another_pod: Callable[[], Pod]) -> Pod:
    """One `hopin serve` that answers `/health`."""
    return another_pod()


@pytest.fixture
def redis(redis_url: str) -> Iterator[Redis]:
    """A client of the Redis the pods share, which reaches it directly and not through the gate."""
    with Redis.from_url(redis_url) as client:
        yield client


@pytest.fixture
def pages() -> Iterator[list[Browser]]:
    opened: list[Browser] = []
    yield opened
    for page in opened:
        for socket in page.sockets:
            socket.close()


@pytest.fixture
def page_on(pages: list[Browser]) -> Callable[[Pod, SeededMember], Browser]:
    """A member's page that reaches the pod; its sockets are closed at the end."""

    def open_page(pod: Pod, member: SeededMember) -> Browser:
        page = Browser(pod, member)
        pages.append(page)
        return page

    return open_page


@pytest.fixture
def web_auth_config() -> WebAuthConfig:
    return build_sign_in_configs(ServerSettings(), TEST_REALM, (), in_thread_stores().rate_limits).web_auth
