from enum import StrEnum

DEFAULT_HOST = "0.0.0.0"  # noqa: S104
DEFAULT_PORT = 8080

ROOM_ID_LENGTH = 8

WEBSOCKET_PATH = "/ws"
CALL_PATH = "/call"
JOIN_PATH = "/join"
HEALTH_PATH = "/health"

STUN_SERVER_URL = "stun:stun.l.google.com:19302"

MAX_PARTICIPANTS_PER_ROOM = 6


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
