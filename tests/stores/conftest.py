import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest
from redis import Redis

from hopin.config.constants import HEALTH_PATH
from hopin.main import main
from tests.stores.support.pod_pair import serving_pod_pair
from tests.stores.support.scratch_database import ScratchDatabase, scratch_database
from tests.stores.support.service_gate import ServiceGate

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from tests.stores.support.pod_pair import Pod
    from tests.support.sign_in_settings import SignInSettings

REDIS_URL_VARIABLE = "HOPIN_TEST_REDIS_URL"
POSTGRES_URL_VARIABLE = "HOPIN_TEST_POSTGRES_URL"
REDIS_DEFAULT_DATABASE_COUNT = 16


@pytest.fixture
def redis_url(worker_id: str) -> Iterator[str]:
    """The Redis the job provides, in a database of this test worker's own and free of any budget a test before left."""
    base_url = os.environ.get(REDIS_URL_VARIABLE)
    if base_url is None:
        pytest.fail(f"{REDIS_URL_VARIABLE} names the Redis these tests run against (the Shared stores job sets it)")
    # Redis offers 16 databases by default, so a run has at most that many xdist workers.
    database = 0 if worker_id == "master" else int(worker_id.removeprefix("gw"))
    if database >= REDIS_DEFAULT_DATABASE_COUNT:
        pytest.fail(f"worker {worker_id} has no Redis database of its own: Redis offers {REDIS_DEFAULT_DATABASE_COUNT}")
    url = urlsplit(base_url)._replace(path=f"/{database}").geturl()
    client = Redis.from_url(url)
    client.flushdb()
    yield url
    client.close()


@pytest.fixture
def redis_gate(redis_url: str) -> Iterator[ServiceGate]:
    gate = ServiceGate(redis_url)
    yield gate
    gate.close()


@pytest.fixture
def empty_database(worker_id: str, tmp_path: Path) -> Iterator[ScratchDatabase]:
    """A database of this test's own on the Postgres the job provides, with no table in it."""
    server_url = os.environ.get(POSTGRES_URL_VARIABLE)
    if server_url is None:
        pytest.fail(
            f"{POSTGRES_URL_VARIABLE} names the Postgres these tests run against (the Shared stores job sets it)"
        )
    with scratch_database(server_url, worker_id, tmp_path) as database:
        yield database


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
