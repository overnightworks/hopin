// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.

export enum PARTICIPATION_SIGNAL {
    AGENT_JOINED = "agent_joined",
    AGENT_LEFT = "agent_left",
    AGENT_REMOVE = "agent_remove",
    AGENT_REFUSED = "agent_refused",
}

export enum AGENT_REFUSAL {
    BAD_NAME = "bad_name",
    NO_SEAT = "no_seat",
    TWO_SEATS = "two_seats",
    BUSY = "busy",
    SPONSOR_AWAY = "sponsor_away",
    GONE = "gone",
    NOT_SPONSOR_OR_HOST = "not_sponsor_or_host",
    INVITATION_ONLY = "invitation_only",
    OWN_ACCOUNT_ONLY = "own_account_only",
}
