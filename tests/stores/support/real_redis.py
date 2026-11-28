"""The Redis the Shared stores job provides, as a fixture and as the server of tests that also run on fakeredis."""

import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest
from redis import Redis

if TYPE_CHECKING:
    from collections.abc import Iterator

REDIS_URL_VARIABLE = "HOPIN_TEST_REDIS_URL"
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


FAKEREDIS = "fakeredis"
ON_FAKEREDIS_AND_REAL_REDIS = pytest.mark.parametrize(
    "redis_server", [FAKEREDIS, pytest.param("real redis", marks=pytest.mark.shared_stores)], indirect=True
)
"""For a module whose tests run on fakeredis and, under the `shared_stores` marker, on the job's real Redis."""
