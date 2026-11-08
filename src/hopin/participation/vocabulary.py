from enum import StrEnum

FEATURE_NAME = "participation"
AGENT_NAME_MAX_CHARACTERS = 32
AGENT_ID_BYTES = 16
AGENTS_PER_ROOM = 2
AGENTS_PER_PROCESS = 32


class ParticipationSignal(StrEnum):
    AGENT_JOINED = "agent_joined"
    AGENT_LEFT = "agent_left"
    AGENT_REMOVE = "agent_remove"
    AGENT_REFUSED = "agent_refused"


class AgentRefusal(StrEnum):
    """Why a dial-in, a leave or a removal is refused; the tools answer with it and a seat's removal is told it."""

    BAD_NAME = "bad_name"
    NO_SEAT = "no_seat"
    TWO_SEATS = "two_seats"
    BUSY = "busy"
    SPONSOR_AWAY = "sponsor_away"
    GONE = "gone"
    NOT_SPONSOR_OR_HOST = "not_sponsor_or_host"
    INVITATION_ONLY = "invitation_only"
    OWN_ACCOUNT_ONLY = "own_account_only"


def _members(vocabulary: type[StrEnum]) -> dict[str, str]:
    return {name: member.value for name, member in vocabulary.__members__.items()}


SIGNAL_TYPES = _members(ParticipationSignal)
# The enums the generator renders into the feature's own browser wire, by their exported name.
BROWSER_ENUMS = {"PARTICIPATION_SIGNAL": ParticipationSignal, "AGENT_REFUSAL": AgentRefusal}
