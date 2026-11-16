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
    AGENT_VOICE = "agent_voice",
    AGENT_STOP = "agent_stop",
    AGENT_GESTURE = "agent_gesture",
    AGENT_SEEING = "agent_seeing",
    AGENT_LOOK_REQUEST = "agent_look_request",
    AGENT_LOOK_PART = "agent_look_part",
    AGENT_LOOK_NONE = "agent_look_none",
    AGENT_LOOKED = "agent_looked",
    AGENT_INVITATION_ENDED = "agent_invitation_ended",
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

export enum INVITATION_OUTCOME {
    NO_ANSWER = "no_answer",
    BUSY = "busy",
    OWNER_ABSENT = "owner_absent",
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

export enum FIGURE_GESTURE {
    WAVE = "wave",
}

export interface Offer {
    agent_key: string;
    name: string;
    pending: boolean;
}

export type AgentJoinedFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_JOINED;
    agent_id: string;
    name: string;
    sponsor: string;
    sponsor_id: string;
    figure?: string;
    gestures?: Record<string, string>;
};

export type AgentLeftFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_LEFT;
    agent_id: string;
};

export type AgentHearingFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_HEARING;
    agent_id: string;
    on: boolean;
};

export type AgentSeeingFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_SEEING;
    agent_id: string;
    on: boolean;
};

export type AgentLookRequestFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST;
    request_id: string;
};

export type AgentLookedFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_LOOKED;
    agent_id: string;
};

export type AgentVoiceFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_VOICE;
    agent_id: string;
    voice_id: string;
};

export type AgentStopFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_STOP;
    agent_id: string;
};

export type AgentGestureFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_GESTURE;
    agent_id: string;
    gesture: FIGURE_GESTURE;
};

export type AgentOffersFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_OFFERS;
    shown: boolean;
    full: boolean;
    offers: Offer[];
};

export type AgentInvitationEndedFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED;
    agent_key: string;
    outcome: INVITATION_OUTCOME;
};

export type AgentLineFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_LINE;
    agent_id: string;
    name: string;
    text: string;
};

export type AgentRefusedFrame = {
    type: PARTICIPATION_SIGNAL.AGENT_REFUSED;
    reason: AGENT_REFUSAL;
};

export type ParticipationFrameByType = {
    [PARTICIPATION_SIGNAL.AGENT_JOINED]: AgentJoinedFrame;
    [PARTICIPATION_SIGNAL.AGENT_LEFT]: AgentLeftFrame;
    [PARTICIPATION_SIGNAL.AGENT_HEARING]: AgentHearingFrame;
    [PARTICIPATION_SIGNAL.AGENT_SEEING]: AgentSeeingFrame;
    [PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST]: AgentLookRequestFrame;
    [PARTICIPATION_SIGNAL.AGENT_LOOKED]: AgentLookedFrame;
    [PARTICIPATION_SIGNAL.AGENT_VOICE]: AgentVoiceFrame;
    [PARTICIPATION_SIGNAL.AGENT_STOP]: AgentStopFrame;
    [PARTICIPATION_SIGNAL.AGENT_GESTURE]: AgentGestureFrame;
    [PARTICIPATION_SIGNAL.AGENT_OFFERS]: AgentOffersFrame;
    [PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED]: AgentInvitationEndedFrame;
    [PARTICIPATION_SIGNAL.AGENT_LINE]: AgentLineFrame;
    [PARTICIPATION_SIGNAL.AGENT_REFUSED]: AgentRefusedFrame;
};

export const HEARING_PATH = "/ws/hearing";
export const VOICE_PATH = "/api/game/voice";
export const LOOK_MAX_PIXELS = 1280;
export const LOOK_ANSWER_SECONDS = 10;
export const LOOK_MAX_BYTES = 524288;
export const LOOK_PART_CHARACTERS = 32768;
