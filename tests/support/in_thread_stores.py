from fakeredis import FakeAsyncRedis, FakeServer
from webauth.rate_limit import SingleProcessRateLimitBackend

from hopin.stores.shared import SharedStores


def in_thread_stores() -> SharedStores:
    """The shared stores of a test that runs one app in its own thread: the library's single-process limiter and a
    Redis kept in memory, one of its own for each call."""
    return SharedStores(
        rate_limits=SingleProcessRateLimitBackend(),
        answers=lambda: True,
        async_redis=FakeAsyncRedis(server=FakeServer()),
    )
