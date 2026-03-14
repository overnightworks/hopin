from enum import StrEnum

DEFAULT_HOST = "0.0.0.0"  # noqa: S104
DEFAULT_PORT = 8080

ROOM_ID_LENGTH = 8

WEBSOCKET_PATH = "/ws"
STREAM_PATH = "/stream"
VIEWER_PATH = "/watch"
HEALTH_PATH = "/health"

STUN_SERVER_URL = "stun:stun.l.google.com:19302"

MAX_VIEWERS_PER_ROOM = 50


class SignalType(StrEnum):
    OFFER = "offer"
    ANSWER = "answer"
    ICE_CANDIDATE = "ice_candidate"
    JOIN = "join"
    LEAVE = "leave"
    ERROR = "error"
    ROOM_CREATED = "room_created"
    VIEWER_JOINED = "viewer_joined"
    VIEWER_LEFT = "viewer_left"
    STREAMER_DISCONNECTED = "streamer_disconnected"


class RoomRole(StrEnum):
    STREAMER = "streamer"
    VIEWER = "viewer"
