"""How a call is laid out in Redis: the key names, the codec of the records and the one script that commits."""

import json
from typing import TYPE_CHECKING, Final

from hopin.config.constants import REDIS_KEY_PREFIX
from hopin.rooms.models import Account, AgentPlace, Departure, HostGrace, Park, Participant, Room, SeatRecord

if TYPE_CHECKING:
    from collections.abc import Mapping

    from hopin.signaling.replay import Supersedes

PREFIX: Final = f"{REDIS_KEY_PREFIX}:call"


def room_key(room_id: str) -> str:
    return f"{PREFIX}:room:{room_id}"


def seat_key(peer_id: str) -> str:
    return f"{PREFIX}:seat:{peer_id}"


def log_key(peer_id: str) -> str:
    return f"{PREFIX}:seat:{peer_id}:log"


def account_key(account_id: str) -> str:
    return f"{PREFIX}:account:{account_id}"


def account_seats_key(account_id: str) -> str:
    return f"{PREFIX}:account:{account_id}:seats"


def hosted_key(account_id: str) -> str:
    return f"{PREFIX}:hosted:{account_id}"


def generation_key(generation: str, index: str) -> str:
    return f"{PREFIX}:generation:{generation}:{index}"


def commit_key(commit_id: str) -> str:
    return f"{PREFIX}:commit:{commit_id}"


# KEYS: the lease's epoch, expiry and generation, then the commit's id. ARGV: the holder, `lasts` in ms, the generation
# the commit runs under, the command and the seconds the commit's result is kept.
COMMIT_SCRIPT = """
local lease = tandem.lease_state({epoch = KEYS[1], expiry = KEYS[2], generation = KEYS[3]}, ARGV[1], tonumber(ARGV[2]))
if not lease or lease[1] ~= 'live' or lease[2] ~= ARGV[3] then
  return cjson.encode({status = 'lease'})
end
local recorded = redis.call('GET', KEYS[4])
if recorded then
  return recorded
end
local command = cjson.decode(ARGV[4])
for _, read in ipairs(command.reads) do
  if tonumber(redis.call('HGET', read[1], 'v') or 0) ~= read[2] then
    return cjson.encode({status = 'conflict'})
  end
end
for _, seat in ipairs(command.fences) do
  if redis.call('HGET', seat, 'gen') ~= ARGV[3] then
    return cjson.encode({status = 'fence'})
  end
end
for _, received in ipairs(command.received) do
  if received[2] <= tonumber(redis.call('HGET', received[1], 'recv') or 0) then
    return cjson.encode({status = 'ok', woken = {}})
  end
end
for _, frame in ipairs(command.frames) do
  if #frame.text > command.max_bytes then
    return cjson.encode({status = 'too_large'})
  end
end
for _, write in ipairs(command.writes) do
  for _, field in ipairs(write.fields) do
    redis.call('HSET', write.key, field[1], field[2])
  end
end
for _, received in ipairs(command.received) do
  redis.call('HSET', received[1], 'recv', received[2])
end
for _, key in ipairs(command.deletes) do
  redis.call('DEL', key)
end
for _, entry in ipairs(command.removes) do
  redis.call('SREM', entry[1], entry[2])
end
for _, entry in ipairs(command.adds) do
  redis.call('SADD', entry[1], entry[2])
end
for _, expiry in ipairs(command.expires) do
  redis.call('EXPIRE', expiry[1], expiry[2])
end
local woken = {}
for _, frame in ipairs(command.frames) do
  if redis.call('EXISTS', frame.seat) == 1 then
    local seq = redis.call('HINCRBY', frame.seat, 'last', 1)
    local supersede = frame.supersede
    if supersede then
      local types = nil
      if supersede.types then
        types = {}
        for _, type in ipairs(supersede.types) do
          types[type] = true
        end
      end
      for _, entry in ipairs(redis.call('XRANGE', frame.log, '-', '+')) do
        local fields = {}
        for index = 1, #entry[2], 2 do
          fields[entry[2][index]] = entry[2][index + 1]
        end
        if (types == nil or types[fields.type])
            and (not supersede.sender or fields.sender == supersede.sender)
            and (not supersede.holding or fields.agent_id == supersede.holding) then
          redis.call('XDEL', frame.log, entry[1])
        end
      end
    end
    redis.call('XADD', frame.log, seq .. '-0',
      'type', frame.type, 'sender', frame.sender, 'agent_id', frame.agent, 'frame', frame.text)
    local excess = redis.call('XLEN', frame.log) - command.limit
    if excess > 0 then
      local oldest = redis.call('XRANGE', frame.log, '-', '+', 'COUNT', excess)
      for _, entry in ipairs(oldest) do
        redis.call('XDEL', frame.log, entry[1])
      end
      redis.call('HSET', frame.seat, 'floor', string.match(oldest[#oldest][1], '^(%d+)'))
    end
    woken[#woken + 1] = frame.peer
  end
end
local reply = cjson.encode({status = 'ok', woken = woken})
redis.call('SET', KEYS[4], reply, 'EX', ARGV[5])
return reply
"""


def supersede_of(supersedes: Supersedes) -> dict[str, object]:
    supersede: dict[str, object] = {}
    if supersedes.types is not None:
        supersede["types"] = sorted(supersedes.types)
    if supersedes.sender is not None:
        supersede["sender"] = supersedes.sender
    if supersedes.holding is not None:
        supersede["holding"] = supersedes.holding[1]
    return supersede


def text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def encode(value: object) -> str:
    # UTF-8 as it is, so a stored frame is no longer than the 4 bytes a character the inbound cap allows.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def encode_room(room: Room) -> str:
    return encode(
        {
            "host": [room.host.id, room.host.name],
            "locked": room.locked,
            "grace": None
            if room.grace is None
            else [room.grace.host_peer_id, room.grace.deadline, room.grace.generation],
            "participants": [[p.participant_id, p.account.id, p.account.name] for p in room.participants],
            "places": [[place.agent_id, place.generation] for place in room.places],
            "departures": [[d.peer_id, d.name, list(d.remaining), d.host_left, d.generation] for d in room.departures],
        }
    )


def decode_room(room_id: str, version: int, data: bytes | str) -> Room:
    stored = json.loads(data)
    grace = stored["grace"]
    return Room(
        room_id=room_id,
        host=Account(*stored["host"]),
        locked=stored["locked"],
        grace=None if grace is None else HostGrace(*grace),
        participants=tuple(Participant(pid, Account(aid, name)) for pid, aid, name in stored["participants"]),
        places=tuple(AgentPlace(*place) for place in stored["places"]),
        departures=tuple(Departure(room_id, p, n, tuple(r), left, g) for p, n, r, left, g in stored["departures"]),
        version=version,
    )


def encode_seat(seat: SeatRecord) -> str:
    return encode(
        {
            "account": [seat.account.id, seat.account.name],
            "token_hash": seat.token_hash.hex(),
            "room_id": seat.room_id,
            "park": None if seat.park is None else seat.park.dropped_at,
        }
    )


def decode_seat(fields: Mapping[bytes | str, bytes | str]) -> SeatRecord:
    stored = json.loads(fields[b"d"])
    return SeatRecord(
        account=Account(*stored["account"]),
        token_hash=bytes.fromhex(stored["token_hash"]),
        generation=text(fields[b"gen"]),
        room_id=stored["room_id"],
        park=None if stored["park"] is None else Park(stored["park"]),
        received_seq=int(fields.get(b"recv", 0)),
        floor=int(fields.get(b"floor", 0)),
        version=int(fields[b"v"]),
    )


def room_index(room: Room | None) -> set[tuple[str, str]]:
    """The index entries a room record stands for: its host's hosted calls, and for each generation the places it took,
    the grace it times and the departures it will tell."""
    if room is None:
        return set()
    entries = {(hosted_key(room.host.id), room.room_id)}
    entries |= {(generation_key(p.generation, "places"), f"{room.room_id}:{p.agent_id}") for p in room.places}
    if room.grace is not None:
        entries.add((generation_key(room.grace.generation, "graces"), room.room_id))
    entries |= {(generation_key(d.generation, "departures"), room.room_id) for d in room.departures}
    return entries


def seat_index(peer_id: str, seat: SeatRecord | None) -> set[tuple[str, str]]:
    if seat is None:
        return set()
    return {(account_seats_key(seat.account.id), peer_id), (generation_key(seat.generation, "seats"), peer_id)}
