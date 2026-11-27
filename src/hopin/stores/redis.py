import structlog
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import ExponentialBackoff, NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.retry import Retry
from webauth.rate_limit import RedisRateLimitBackend

from hopin.config.constants import (
    REDIS_KEY_PREFIX,
    REDIS_RETRY_ATTEMPTS,
    REDIS_SOCKET_TIMEOUT_SECONDS,
)
from hopin.stores.shared import SharedStores

logger = structlog.get_logger()


def connect_redis(url: str) -> Redis:
    """The sync client over HopIn's Redis; it connects on first use, and a Redis that is down fails at once."""
    return Redis.from_url(
        url,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        retry=Retry(NoBackoff(), 0),
    )


def connect_async_redis(url: str) -> AsyncRedis:
    """The async client over HopIn's Redis, which the connector's place store uses, with the same URL and socket
    timeouts. Without the timeouts a publish to a
    silent Redis waits for ever, and without the retry the first read after a Redis restart can fail on a stale pooled
    connection. It does not decode responses, which would drop the connection on a payload that is no UTF-8."""
    return AsyncRedis.from_url(
        url,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        retry=AsyncRetry(ExponentialBackoff(), REDIS_RETRY_ATTEMPTS),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )


def redis_answers(client: Redis) -> bool:
    try:
        return bool(client.ping())
    except RedisError:
        logger.warning("redis.refused", reason="not_answering")
        return False


def connect_shared_stores(url: str) -> SharedStores:
    client = connect_redis(url)
    return SharedStores(
        rate_limits=RedisRateLimitBackend(client, key_prefix=f"{REDIS_KEY_PREFIX}:rate"),
        answers=lambda: redis_answers(client),
        async_redis=connect_async_redis(url),
    )
