from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from redis.asyncio import Redis as AsyncRedis
    from webauth.ports import RateLimitBackend


@dataclass(frozen=True)
class SharedStores:
    """What the processes of one deployment share, as the app reads it."""

    rate_limits: RateLimitBackend
    # Whether the shared store answers now; `/health` is not ready while it does not.
    answers: Callable[[], bool]
    # The async client over the shared Redis, for what the connector keeps in it.
    async_redis: AsyncRedis
