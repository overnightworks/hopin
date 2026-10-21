from enum import StrEnum

from webauth.policies import PathRules

DEFAULT_HOST = "0.0.0.0"  # noqa: S104
DEFAULT_PORT = 8080

ROOM_ID_LENGTH = 8

WEBSOCKET_PATH = "/ws"
HOME_PATH = "/"
CALL_PATH = "/c"
HEALTH_PATH = "/health"
STATIC_PATH = "/static"
ICE_SERVERS_PATH = "/api/ice-servers"
ACCOUNT_PATH = "/api/account"
AUTH_START_PATH = "/auth/start"
AUTH_CALLBACK_PATH = "/auth/callback"
LOGIN_PATH = "/login"
NO_ACCESS_PATH = "/no-access"
LOGOUT_PATH = "/logout"
BACKCHANNEL_LOGOUT_PATH = "/auth/backchannel-logout"
BACKCHANNEL_LOGOUT_PATH_RULES = PathRules(exact=frozenset({BACKCHANNEL_LOGOUT_PATH}))
DEFAULT_REQUEST_BODY_MAX_BYTES = 4 * 1024
BACKCHANNEL_LOGOUT_BODY_MAX_BYTES = 32 * 1024

PERMISSIONS_POLICY = "camera=(self), microphone=(self), display-capture=(self), geolocation=()"

OIDC_CLIENT_ID = "hopin"
LOGIN_GROUP = "/apps/hopin"
RETURN_TO_QUERY_PARAMETER = "next"
OIDC_SCOPES = ("openid", "profile", "groups")
OIDC_LEEWAY_SECONDS = 30
SESSION_LIFETIME_SECONDS = 12 * 60 * 60

STUN_SERVER_URL = "stun:stun.cloudflare.com:3478"

MIN_PARTICIPANTS_PER_ROOM = 2
MAX_PARTICIPANTS_PER_ROOM = 6
HOST_GRACE_SECONDS = 60
RESUME_WINDOW_SECONDS = 30
RESUME_TOKEN_BYTES = 32
REPLAY_LOG_LIMIT = 256
MAX_SIGNALING_FRAME_LENGTH = 65536

WEBSOCKET_DISCONNECT_EVENT = "websocket.disconnect"
# 1005 means no status arrived, from a dropped connection or a close frame without a code;
# HopIn's page always sends 1000, so 1005 is a loss and the seat parks.
NORMAL_CLOSE_CODES = frozenset({1000, 1001})
POLICY_VIOLATION_CLOSE_CODE = 1008
# The server closes a socket that no longer serves a seat: a resume took the seat over,
# a resume could not catch the seat up, or the room ended.
SEAT_CLOSED_BY_SERVER_CODE = 4001
PEER_ID_BYTES = 16
UVICORN_WEBSOCKET_IMPLEMENTATION = "websockets-sansio"
UVICORN_LOG_LEVEL = "warning"


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
    MEDIA_STATE = "media_state"
    HOST_AWAY = "host_away"
    HOST_BACK = "host_back"
    END_ROOM = "end_room"
    RESUME = "resume"
    RESUMED = "resumed"


class ErrorReason(StrEnum):
    NOT_FOUND = "not_found"
    LOCKED = "locked"
    FULL = "full"
    NOT_HOST = "not_host"
    INVALID_MESSAGE = "invalid_message"
    RESUME_REFUSED = "resume_refused"


class RoomClosedReason(StrEnum):
    HOST_GONE = "host_gone"
    ENDED = "ended"


class ResumeRefusalCause(StrEnum):
    NOT_FIRST_FRAME = "not_first_frame"
    MALFORMED = "malformed"
    UNKNOWN_SEAT = "unknown_seat"
    CREDENTIAL_MISMATCH = "credential_mismatch"
    WRONG_ACCOUNT = "wrong_account"
    SERVER_LOG_TOO_SHORT = "server_log_too_short"
    BROWSER_LOG_TOO_SHORT = "browser_log_too_short"
