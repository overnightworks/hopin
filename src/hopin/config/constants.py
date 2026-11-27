from dataclasses import dataclass
from enum import StrEnum

from hopin.config.rate import Rate

DEFAULT_HOST = "0.0.0.0"  # noqa: S104 - the container must listen on every interface and HOPIN_HOST overrides it
DEFAULT_PORT = 8080

ROOM_ID_LENGTH = 8

WEBSOCKET_PATH = "/ws"
HOME_PATH = "/"
CALL_PATH = "/c"
# The one call address that is no room: the stage's start link; no room id equals its last segment.
START_LINK_PATH = f"{CALL_PATH}/new"
HEALTH_PATH = "/health"
APP_ASSET_PATH = "/_app"
IMMUTABLE_ASSET_DIRECTORY = "immutable"
ICE_SERVERS_PATH = "/api/ice-servers"
CALL_FEATURES_PATH = "/api/call-features"
ACCOUNT_PATH = "/api/account"
ACCOUNT_CONSOLE_PATH = "/account"
PEOPLE_PATH = "/api/admin/people"
PERSON_PATH = f"{PEOPLE_PATH}/{{subject}}"
PERSON_EXPORT_PATH = f"{PERSON_PATH}/export"
AUTH_START_PATH = "/auth/start"
AUTH_CALLBACK_PATH = "/auth/callback"
LOGIN_PATH = "/login"
NO_ACCESS_PATH = "/no-access"
LOGOUT_PATH = "/logout"
BACKCHANNEL_LOGOUT_PATH = "/auth/backchannel-logout"
READ_METHODS = ["GET", "HEAD"]
# Private, because the assets sit behind the sign-in and a shared cache would hand them out past it (RFC 9111 5.2.2.9).
IMMUTABLE_ASSET_CACHE_CONTROL = "private, max-age=31536000, immutable"

PERMISSIONS_POLICY = "camera=(self), microphone=(self), display-capture=(self), geolocation=()"

PEOPLE_EXPORT_VERSION = 1
PEOPLE_ERASURE_VERSION = 1
EXPORT_APP_NAME = "hopin"
OIDC_SCOPES = ("openid", "profile", "groups")
OIDC_LEEWAY_SECONDS = 30
SESSION_LIFETIME_SECONDS = 12 * 60 * 60
SESSION_SWEEP_SECONDS = 10 * 60

STUN_SERVER_URL = "stun:stun.cloudflare.com:3478"
# A call ends with its sign-in, so one credential per call start covers the whole call.
TURN_CREDENTIAL_TTL_SECONDS = 12 * 60 * 60

MIN_PARTICIPANTS_PER_ROOM = 2
MAX_PARTICIPANTS_PER_ROOM = 6
# Bounds one account's /ws sockets, which webauth's HTTP rate limit does not cover; above the few devices a person uses.
MAX_LIVE_SEATS_PER_ACCOUNT = 8
HOST_GRACE_SECONDS = 60
RESUME_WINDOW_SECONDS = 30
SESSION_RECHECK_SECONDS = 60
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
# The server closes a socket whose sign-in ended; 1011 closes one whose sign-in check itself failed.
SESSION_ENDED_CLOSE_CODE = 4002
SERVER_ERROR_CLOSE_CODE = 1011
PEER_ID_BYTES = 16

UVICORN_WEBSOCKET_IMPLEMENTATION = "websockets-sansio"


@dataclass(frozen=True)
class LineLimits:
    """What a chat line may be, for every writer and reader of one: the page's composer, an agent's line and an
    agent's spoken sentence. A line is 1 to `max_characters` code points once `trimmed` characters are cut from its
    ends, and holds no `refused` character. A person's lines to one reader hold within a budget of `burst_lines`, which
    refills at `lines_per_second`: the sender paces its own lines to it and every receiver, the server included, drops
    a line beyond it."""

    max_characters: int
    trimmed: frozenset[str]
    refused: frozenset[str]
    burst_lines: int
    lines_per_second: float


# Explicit sets, because `str.strip()` and `String.trim()` differ (U+FEFF) and the page and the server must judge a
# line alike. The trimmed set is Python's whitespace plus the byte order mark, the refused set the control
# characters (Unicode category Cc, line breaks and tabs included).
LINE_LIMITS = LineLimits(
    max_characters=600,
    trimmed=frozenset(
        "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
        "\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
    ),
    refused=frozenset(map(chr, (*range(0x00, 0x20), *range(0x7F, 0xA0)))),
    burst_lines=10,
    lines_per_second=1,
)


@dataclass(frozen=True)
class FileLimits:
    """What a file posted in a call may be, enforced by every receiving browser: the sender's composer refuses a file
    over `max_bytes`, a receiver refuses an announcement over it, refuses a fetch while the same sender has
    `max_in_flight` others in flight, and refuses one that would take its received bytes in this page's call beyond
    `max_received_bytes`. A sender serves at most `max_in_flight` channels to one receiver at a time. A
    picture announced as at most `picture_auto_fetch_bytes` is fetched at once, any other file on a tap. A shown
    name holds at most `max_name_characters` code points."""

    max_bytes: int
    max_in_flight: int
    max_received_bytes: int
    picture_auto_fetch_bytes: int
    max_name_characters: int


FILE_LIMITS = FileLimits(
    max_bytes=32 * 1024 * 1024,
    max_in_flight=1,
    max_received_bytes=256 * 1024 * 1024,
    picture_auto_fetch_bytes=4 * 1024 * 1024,
    max_name_characters=100,
)


class TurnProvider(StrEnum):
    OFF = "off"
    CLOUDFLARE = "cloudflare"


class SignalType(StrEnum):
    OFFER = "offer"
    ANSWER = "answer"
    ICE_CANDIDATE = "ice_candidate"
    JOIN = "join"
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
    MEDIA_STATE = "media_state"
    CONNECTION_OUTCOME = "connection_outcome"
    HOST_AWAY = "host_away"
    HOST_BACK = "host_back"
    END_ROOM = "end_room"
    RESUME = "resume"
    RESUMED = "resumed"


class ConnectionOutcome(StrEnum):
    """How a seat's peer connection to one other seat went, as the browser can observe it: the connection came up
    straight (`direct`) or through a TURN relay (`relayed`), it was lost for good (`failed`), or it came back after a
    drop (`resumed`)."""

    DIRECT = "direct"
    RELAYED = "relayed"
    FAILED = "failed"
    RESUMED = "resumed"


# A seat's reports that the server writes down in a window; a longer burst is dropped.
# Room for each other seat's connection to go direct, fail and resume once in a full room.
CONNECTION_OUTCOME_RATE = Rate(most=(MAX_PARTICIPANTS_PER_ROOM - 1) * 3, window_seconds=60)


class ErrorReason(StrEnum):
    NOT_FOUND = "not_found"
    LOCKED = "locked"
    FULL = "full"
    NOT_HOST = "not_host"
    INVALID_MESSAGE = "invalid_message"
    RESUME_REFUSED = "resume_refused"
    TOO_MANY_CALLS = "too_many_calls"


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


# A request holds its worker thread until the Redis client gives up (webauth's rate limits), so the wait is short.
REDIS_SOCKET_TIMEOUT_SECONDS = 1.0
REDIS_KEY_PREFIX = "hopin:v1"
# agents keeps its keys under `<prefix>-agents`, so the one segment that is HopIn's own name keeps them from every other
# key of this Redis.
REDIS_AGENTS_KEY_PREFIX = "hopin"
# How often the async client retries a connection before it gives up.
REDIS_RETRY_ATTEMPTS = 3

# One integer per advisory lock of the database, each distinct from the others: any constant shared by every process
# that takes the lock does.
# Serialises concurrent `hopin migrate` runs.
MIGRATION_LOCK_KEY = 7_426_113_501
# Serialises the sign-in entries of every pod on the database (webauth's `sign_in_entry`).
SIGN_IN_LOCK_KEY = 7_426_113_502
POSTGRES_DRIVER = "postgresql+psycopg"
# A pod holds at most pool_size + max_overflow connections, so the database's connection limit is sized from these.
DATABASE_POOL_SIZE = 5
DATABASE_POOL_OVERFLOW = 5
# How long a request waits for a free pooled connection.
DATABASE_POOL_TIMEOUT_SECONDS = 10
# Bounds a connection attempt to a database that is down or silent; libpq takes whole seconds.
DATABASE_CONNECT_TIMEOUT_SECONDS = 5
