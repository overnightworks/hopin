from enum import IntEnum, StrEnum

FEATURE_NAME = "participation"
# The page installs this browser module beside the feature's own when agents may show figures.
FIGURE_MODULE = "figure"
AGENT_NAME_MAX_CHARACTERS = 32
AGENT_LINE_MAX_CHARACTERS = 600
LINES_PER_WINDOW = 10
LINE_WINDOW_SECONDS = 60
AGENT_ID_BYTES = 16
AGENTS_PER_ROOM = 2
AGENTS_PER_PROCESS = 32
FIGURE_PATH = "/api/participation/figures"
FIGURE_FETCH_SECONDS = 30
# A server bound at least as large as the figure profile's file budget (12 MiB in figure v0.1.0), so every figure
# the profile admits fits.
FIGURE_MAX_BYTES = 12 * 1024 * 1024
FIGURE_CACHE_BYTES = 48 * 1024 * 1024
FIGURE_FETCH_WORKERS = 2
FIGURE_REDIRECTS = 1
# The hearing socket of a seat is this path and the seat's id.
HEARING_PATH = "/ws/hearing"
# A voice is fetched at this path and the voice's id; the game's lines and an agent's sentences are served alike.
VOICE_PATH = "/api/game/voice"


class ParticipationSignal(StrEnum):
    AGENT_JOINED = "agent_joined"
    AGENT_LEFT = "agent_left"
    AGENT_REMOVE = "agent_remove"
    AGENT_REFUSED = "agent_refused"
    AGENT_OFFERS = "agent_offers"
    AGENT_ADD = "agent_add"
    AGENT_LINE = "agent_line"
    AGENT_HEARING = "agent_hearing"
    AGENT_VOICE = "agent_voice"


class AgentRefusal(StrEnum):
    """Why a dial-in, an add, an accept, a leave, a removal, a line or a hearing switch is refused; the tools answer
    with it and a seat is told it."""

    BAD_NAME = "bad_name"
    NO_SEAT = "no_seat"
    TWO_SEATS = "two_seats"
    BUSY = "busy"
    SPONSOR_AWAY = "sponsor_away"
    GONE = "gone"
    NOT_SPONSOR_OR_HOST = "not_sponsor_or_host"
    NOT_SPONSOR = "not_sponsor"
    INVITATION_ONLY = "invitation_only"
    OWN_ACCOUNT_ONLY = "own_account_only"
    NOT_ADDER = "not_adder"
    ALREADY_THERE = "already_there"
    STALE_INVITATION = "stale_invitation"
    UNAVAILABLE = "unavailable"
    BAD_LINE = "bad_line"
    TOO_FAST = "too_fast"


class HearingRefusal(StrEnum):
    """Why a seat gets no handle to be heard through: the account does not hold a seat in a room under that id, the
    seat waits to resume, or no joined agent hears it."""

    NO_SEAT = "no_seat"
    SEAT_AWAY = "seat_away"
    NOBODY_HEARS = "nobody_hears"


class HearingClose(IntEnum):
    """How a hearing socket closes, as the page reads it: the refusals of a socket that never opened a hop, then the
    ways a hop ends. A hop's own code is 4000 plus the HTTP status nearest its meaning."""

    NO_SEAT = 4404
    SEAT_AWAY = 4409
    NOBODY_HEARS = 4403
    NO_TOKEN = 4503
    # The hop's endings are the codes the speech client's relay closes the socket with; they are named here for the
    # page, and a test holds each equal to the client's.
    NORMAL = 1000
    TEXT_FRAME = 4400
    FRAME_TOO_LARGE = 4413
    OVER_CAP = 4507
    BUSY = 4429
    PAST_DEADLINE = 4504
    SERVICE_FAILED = 4502


class FigureRefusal(StrEnum):
    """Why an agent's figure is not kept at its accept or not fetched from its source; logged, never told a seat."""

    NO_SOURCE = "no_source"
    ALTERED_BY_PARSING = "altered_by_parsing"
    NOT_HTTPS = "not_https"
    QUERY = "query"
    FRAGMENT = "fragment"
    PORT = "port"
    UPPERCASE_HOST = "uppercase_host"
    OTHER_HOST = "other_host"
    PERCENT = "percent"
    NON_ASCII = "non_ascii"
    DISALLOWED_CHARACTER = "disallowed_character"
    EMPTY_SEGMENT = "empty_segment"
    DOT_SEGMENT = "dot_segment"
    DOT_DOT_SEGMENT = "dot_dot_segment"
    OUTSIDE_PREFIX = "outside_prefix"
    UNREACHABLE = "unreachable"
    NOT_OK = "not_ok"
    SECOND_REDIRECT = "second_redirect"
    REDIRECT_NOT_HTTPS = "redirect_not_https"
    MALFORMED_URL = "malformed_url"
    PAST_DEADLINE = "past_deadline"
    TOO_LARGE = "too_large"
    MISMATCH = "mismatch"
    BAD_HEADER = "bad_header"


def _members(vocabulary: type[StrEnum]) -> dict[str, str]:
    return {name: member.value for name, member in vocabulary.__members__.items()}


SIGNAL_TYPES = _members(ParticipationSignal)
# The enums the generator renders into the feature's own browser wire, by their exported name.
BROWSER_ENUMS = {
    "PARTICIPATION_SIGNAL": ParticipationSignal,
    "AGENT_REFUSAL": AgentRefusal,
    "HEARING_CLOSE": HearingClose,
}
