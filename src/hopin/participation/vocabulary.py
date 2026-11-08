from enum import StrEnum

FEATURE_NAME = "participation"
AGENT_NAME_MAX_CHARACTERS = 32
AGENT_LINE_MAX_CHARACTERS = 600
LINES_PER_WINDOW = 10
LINE_WINDOW_SECONDS = 60
AGENT_ID_BYTES = 16
AGENTS_PER_ROOM = 2
AGENTS_PER_PROCESS = 32
INVITATION_SECONDS = 120


class ParticipationSignal(StrEnum):
    AGENT_JOINED = "agent_joined"
    AGENT_LEFT = "agent_left"
    AGENT_REMOVE = "agent_remove"
    AGENT_REFUSED = "agent_refused"
    AGENT_OFFERS = "agent_offers"
    AGENT_ADD = "agent_add"
    AGENT_LINE = "agent_line"


class AgentRefusal(StrEnum):
    """Why a dial-in, an add, an accept, a leave, a removal or a line is refused; the tools answer with it and a seat is
    told it."""

    BAD_NAME = "bad_name"
    NO_SEAT = "no_seat"
    TWO_SEATS = "two_seats"
    BUSY = "busy"
    SPONSOR_AWAY = "sponsor_away"
    GONE = "gone"
    NOT_SPONSOR_OR_HOST = "not_sponsor_or_host"
    INVITATION_ONLY = "invitation_only"
    OWN_ACCOUNT_ONLY = "own_account_only"
    NOT_ADDER = "not_adder"
    ALREADY_THERE = "already_there"
    STALE_INVITATION = "stale_invitation"
    UNAVAILABLE = "unavailable"
    BAD_LINE = "bad_line"
    TOO_FAST = "too_fast"


def _members(vocabulary: type[StrEnum]) -> dict[str, str]:
    return {name: member.value for name, member in vocabulary.__members__.items()}


SIGNAL_TYPES = _members(ParticipationSignal)
# The enums the generator renders into the feature's own browser wire, by their exported name.
BROWSER_ENUMS = {"PARTICIPATION_SIGNAL": ParticipationSignal, "AGENT_REFUSAL": AgentRefusal}
