// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.

export enum PARTICIPATION_SIGNAL {
    AGENT_JOINED = "agent_joined",
    AGENT_LEFT = "agent_left",
    AGENT_REMOVE = "agent_remove",
    AGENT_REFUSED = "agent_refused",
    AGENT_OFFERS = "agent_offers",
    AGENT_ADD = "agent_add",
    AGENT_LINE = "agent_line",
    AGENT_HEARING = "agent_hearing",
}

export enum AGENT_REFUSAL {
    BAD_NAME = "bad_name",
    NO_SEAT = "no_seat",
    TWO_SEATS = "two_seats",
    BUSY = "busy",
    SPONSOR_AWAY = "sponsor_away",
    GONE = "gone",
    NOT_SPONSOR_OR_HOST = "not_sponsor_or_host",
    NOT_SPONSOR = "not_sponsor",
    INVITATION_ONLY = "invitation_only",
    OWN_ACCOUNT_ONLY = "own_account_only",
    NOT_ADDER = "not_adder",
    ALREADY_THERE = "already_there",
    STALE_INVITATION = "stale_invitation",
    UNAVAILABLE = "unavailable",
    BAD_LINE = "bad_line",
    TOO_FAST = "too_fast",
}

export enum HEARING_CLOSE {
    NO_SEAT = 4404,
    SEAT_AWAY = 4409,
    NOBODY_HEARS = 4403,
    NO_TOKEN = 4503,
    NORMAL = 1000,
    TEXT_FRAME = 4400,
    FRAME_TOO_LARGE = 4413,
    OVER_CAP = 4507,
    BUSY = 4429,
    PAST_DEADLINE = 4504,
    SERVICE_FAILED = 4502,
}

export const HEARING_PATH = "/ws/hearing";
