import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest
from redis import Redis

from hopin.config.constants import HEALTH_PATH
from tests.stores.support.pod_pair import serving_pod_pair
from tests.stores.support.redis_gate import RedisGate

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from tests.stores.support.pod_pair import Pod
    from tests.support.sign_in_settings import SignInSettings

REDIS_URL_VARIABLE = "HOPIN_TEST_REDIS_URL"


@pytest.fixture
def redis_url(worker_id: str) -> Iterator[str]:
    """The Redis the job provides, in a database of this test worker's own and free of any budget a test before left."""
    base_url = os.environ.get(REDIS_URL_VARIABLE)
    if base_url is None:
        pytest.fail(f"{REDIS_URL_VARIABLE} names the Redis these tests run against (the Shared stores job sets it)")
    database = 0 if worker_id == "master" else int(worker_id.removeprefix("gw"))
    url = urlsplit(base_url)._replace(path=f"/{database}").geturl()
    client = Redis.from_url(url)
    client.flushdb()
    yield url
    client.close()


@pytest.fixture
def redis_gate(redis_url: str) -> Iterator[RedisGate]:
    gate = RedisGate(redis_url)
    yield gate
    gate.close()


@pytest.fixture
def pods(
    sign_in_settings: Callable[..., SignInSettings], redis_gate: RedisGate, tmp_path: Path
) -> Iterator[tuple[Pod, Pod]]:
    with serving_pod_pair(sign_in_settings().environment, redis_gate.url, tmp_path, HEALTH_PATH) as pair:
        yield pair
