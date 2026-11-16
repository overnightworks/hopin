import structlog
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from webauth.rate_limit import RedisRateLimitBackend

from hopin.config.constants import REDIS_KEY_PREFIX, REDIS_SOCKET_TIMEOUT_SECONDS
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


def redis_answers(client: Redis) -> bool:
    try:
        return bool(client.ping())
    except RedisError:
        logger.warning("redis_not_answering")
        return False


def connect_shared_stores(url: str) -> SharedStores:
    client = connect_redis(url)
    return SharedStores(
        rate_limits=RedisRateLimitBackend(client, key_prefix=f"{REDIS_KEY_PREFIX}:rate"),
        answers=lambda: redis_answers(client),
    )
