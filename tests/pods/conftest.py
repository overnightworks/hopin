import os
from typing import TYPE_CHECKING

import pytest
from redis import Redis

from hopin.config.constants import HEALTH_PATH, REDIS_KEY_PREFIX
from tests.pods.support.pod_pair import serving_pod_pair
from tests.pods.support.redis_gate import RedisGate

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from tests.pods.support.pod_pair import Pod
    from tests.support.sign_in_settings import SignInSettings

REDIS_URL_VARIABLE = "HOPIN_TEST_REDIS_URL"


@pytest.fixture
def redis_url() -> Iterator[str]:
    """The Redis the job provides, free of any budget a test before this one left."""
    url = os.environ.get(REDIS_URL_VARIABLE)
    if url is None:
        pytest.fail(f"{REDIS_URL_VARIABLE} names the Redis these tests run against (the Shared stores job sets it)")
    client = Redis.from_url(url)
    for key in client.scan_iter(f"{REDIS_KEY_PREFIX}:*"):
        client.delete(key)
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
