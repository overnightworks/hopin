from enum import StrEnum

DEFAULT_HOST = "0.0.0.0"  # noqa: S104
DEFAULT_PORT = 8080

ROOM_ID_LENGTH = 8

WEBSOCKET_PATH = "/ws"
CALL_PATH = "/call"
JOIN_PATH = "/join"
HEALTH_PATH = "/health"
STATIC_PATH = "/static"

STUN_SERVER_URL = "stun:stun.l.google.com:19302"

MAX_PARTICIPANTS_PER_ROOM = 6

WEBSOCKET_DISCONNECT_EVENT = "websocket.disconnect"
# 1005 is a close without a code, which is how HopIn's own client leaves.
NORMAL_CLOSE_CODES = frozenset({1000, 1001, 1005})
NORMAL_CLOSE_CODE = 1000
UVICORN_WEBSOCKET_IMPLEMENTATION = "websockets-sansio"


class SignalType(StrEnum):
    OFFER = "offer"
    ANSWER = "answer"
    ICE_CANDIDATE = "ice_candidate"
    JOIN = "join"
    LEAVE = "leave"
    ERROR = "error"
    ROOM_CREATED = "room_created"
    PARTICIPANT_JOINED = "participant_joined"
    PARTICIPANT_LEFT = "participant_left"
    ROOM_CLOSED = "room_closed"
    EXISTING_PARTICIPANTS = "existing_participants"
    LOCK_ROOM = "lock_room"
    UNLOCK_ROOM = "unlock_room"
    ROOM_LOCKED = "room_locked"
    ROOM_UNLOCKED = "room_unlocked"
    CHAT = "chat"
