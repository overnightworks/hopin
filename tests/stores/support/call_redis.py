"""What a call keeps in a real Redis, read from the test the way an operator would: keys, records and indices."""

from typing import TYPE_CHECKING

from hopin.signaling.call_format import account_seats_key, decode_room, decode_seat, room_key, seat_key

if TYPE_CHECKING:
    from redis import Redis

    from hopin.rooms.models import Room, SeatRecord

CALL_KEY_PATTERN = "hopin:v1:call:*"
NO_EXPIRY = -1


def call_keys(redis: Redis) -> set[bytes]:
    """The call's keys that no expiry ends: the seats, rooms and indices, which only a leave or a lapse removes."""
    return {key for key in redis.scan_iter(CALL_KEY_PATTERN) if redis.ttl(key) == NO_EXPIRY}


def _content(redis: Redis, key: bytes) -> str:
    read = {
        b"string": redis.get,
        b"hash": redis.hgetall,
        b"set": redis.smembers,
        b"zset": lambda name: redis.zrange(name, 0, -1, withscores=True),
        b"list": lambda name: redis.lrange(name, 0, -1),
        b"stream": lambda name: redis.xrange(name),
    }
    return repr(read[redis.type(key)](key))


def everything_stored(redis: Redis) -> bytes:
    """Every key's name and content, whatever its type."""
    return b"".join(key + _content(redis, key).encode() for key in redis.scan_iter())


def call_state(redis: Redis) -> dict[bytes, str]:
    """The content of each key in `call_keys`: two readings are equal when the call changed nowhere between them."""
    return {key: _content(redis, key) for key in call_keys(redis)}


def keys_naming(redis: Redis, generation: str) -> set[bytes]:
    """The call's keys, expiring ones too, that name a process generation."""
    return {key for key in redis.scan_iter(CALL_KEY_PATTERN) if generation.encode() in key}


def seat_record(redis: Redis, peer_id: str) -> SeatRecord | None:
    fields = redis.hgetall(seat_key(peer_id))
    return decode_seat(fields) if fields else None


def room_record(redis: Redis, room_id: str) -> Room | None:
    fields = redis.hgetall(room_key(room_id))
    return decode_room(room_id, int(fields[b"v"]), fields[b"d"]) if fields else None


def seats_held(redis: Redis, account_id: str) -> int:
    """The seats the account's index holds, on every process: what the cap counts."""
    return int(redis.scard(account_seats_key(account_id)))
