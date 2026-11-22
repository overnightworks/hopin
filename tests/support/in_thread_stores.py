from time import monotonic
from typing import TYPE_CHECKING

from fakeredis import FakeAsyncRedis, FakeServer
from webauth.rate_limit import SingleProcessRateLimitBackend

from hopin.stores.shared import SharedStores

if TYPE_CHECKING:
    from collections.abc import Callable


def in_thread_stores(monotonic_clock: Callable[[], float] = monotonic) -> SharedStores:
    """The shared stores of a test that runs one app in its own thread: the library's single-process limiter and a
    Redis kept in memory, one of its own for each call. The limiter reads `monotonic_clock`."""
    return SharedStores(
        rate_limits=SingleProcessRateLimitBackend(monotonic_clock=monotonic_clock),
        answers=lambda: True,
        async_redis=FakeAsyncRedis(server=FakeServer()),
    )
