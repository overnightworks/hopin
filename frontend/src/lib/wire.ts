// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.

export enum SIGNAL_TYPE {
    OFFER = "offer",
    ANSWER = "answer",
    ICE_CANDIDATE = "ice_candidate",
    JOIN = "join",
    ERROR = "error",
    ROOM_CREATED = "room_created",
    PARTICIPANT_JOINED = "participant_joined",
    PARTICIPANT_LEFT = "participant_left",
    ROOM_CLOSED = "room_closed",
    EXISTING_PARTICIPANTS = "existing_participants",
    LOCK_ROOM = "lock_room",
    UNLOCK_ROOM = "unlock_room",
    ROOM_LOCKED = "room_locked",
    ROOM_UNLOCKED = "room_unlocked",
    CHAT = "chat",
    MEDIA_STATE = "media_state",
    HOST_AWAY = "host_away",
    HOST_BACK = "host_back",
    END_ROOM = "end_room",
    RESUME = "resume",
    RESUMED = "resumed",
}

export enum ERROR_REASON {
    NOT_FOUND = "not_found",
    LOCKED = "locked",
    FULL = "full",
    NOT_HOST = "not_host",
    INVALID_MESSAGE = "invalid_message",
    RESUME_REFUSED = "resume_refused",
    TOO_MANY_CALLS = "too_many_calls",
}

export enum ROOM_CLOSED_REASON {
    HOST_GONE = "host_gone",
    ENDED = "ended",
}

export type RoomClosedFrame = {
    type: SIGNAL_TYPE.ROOM_CLOSED;
    reason: ROOM_CLOSED_REASON;
    grace_seconds?: number;
};

export type ErrorFrame = {
    type: SIGNAL_TYPE.ERROR;
    reason: ERROR_REASON;
    message: string;
    capacity?: number;
    limit?: number;
};

export type RoomCreatedFrame = {
    type: SIGNAL_TYPE.ROOM_CREATED;
    room_id: string;
    is_host: boolean;
    participant_id: string;
    resume_token: string;
};

export type ExistingParticipantsFrame = {
    type: SIGNAL_TYPE.EXISTING_PARTICIPANTS;
    participants: string[];
    names: Record<string, string>;
    is_host: boolean;
    locked: boolean;
    participant_id: string;
    resume_token: string;
};

export type ParticipantJoinedFrame = {
    type: SIGNAL_TYPE.PARTICIPANT_JOINED;
    participant_id: string;
    name: string;
};

export type ParticipantLeftFrame = {
    type: SIGNAL_TYPE.PARTICIPANT_LEFT;
    participant_id: string;
};

export type RoomLockedFrame = {
    type: SIGNAL_TYPE.ROOM_LOCKED;
};

export type RoomUnlockedFrame = {
    type: SIGNAL_TYPE.ROOM_UNLOCKED;
};

export type ChatBroadcastFrame = {
    type: SIGNAL_TYPE.CHAT;
    message: string;
    from: string;
};

export type MediaStateBroadcastFrame = {
    type: SIGNAL_TYPE.MEDIA_STATE;
    from: string;
    audio: boolean;
    video: boolean;
};

export type OfferRelayFrame = {
    type: SIGNAL_TYPE.OFFER;
    sdp: unknown;
    candidate: unknown;
    from: string;
};

export type AnswerRelayFrame = {
    type: SIGNAL_TYPE.ANSWER;
    sdp: unknown;
    candidate: unknown;
    from: string;
};

export type IceCandidateRelayFrame = {
    type: SIGNAL_TYPE.ICE_CANDIDATE;
    sdp: unknown;
    candidate: unknown;
    from: string;
};

export type ResumedFrame = {
    type: SIGNAL_TYPE.RESUMED;
    resume_token: string;
    last_seq: number;
};

export type ServerFrameByType = {
    [SIGNAL_TYPE.ROOM_CLOSED]: RoomClosedFrame;
    [SIGNAL_TYPE.ERROR]: ErrorFrame;
    [SIGNAL_TYPE.ROOM_CREATED]: RoomCreatedFrame;
    [SIGNAL_TYPE.EXISTING_PARTICIPANTS]: ExistingParticipantsFrame;
    [SIGNAL_TYPE.PARTICIPANT_JOINED]: ParticipantJoinedFrame;
    [SIGNAL_TYPE.PARTICIPANT_LEFT]: ParticipantLeftFrame;
    [SIGNAL_TYPE.ROOM_LOCKED]: RoomLockedFrame;
    [SIGNAL_TYPE.ROOM_UNLOCKED]: RoomUnlockedFrame;
    [SIGNAL_TYPE.CHAT]: ChatBroadcastFrame;
    [SIGNAL_TYPE.MEDIA_STATE]: MediaStateBroadcastFrame;
    [SIGNAL_TYPE.OFFER]: OfferRelayFrame;
    [SIGNAL_TYPE.ANSWER]: AnswerRelayFrame;
    [SIGNAL_TYPE.ICE_CANDIDATE]: IceCandidateRelayFrame;
    [SIGNAL_TYPE.RESUMED]: ResumedFrame;
};
export type ServerFrame = ServerFrameByType[keyof ServerFrameByType];
export type SequencedFrame = ServerFrame & { seq?: number };

export const SESSION_ENDED_CLOSE_CODE = 4002;
export const WEBSOCKET_PATH = "/ws";
export const ICE_SERVERS_PATH = "/api/ice-servers";
export const CALL_FEATURES_PATH = "/api/call-features";
export const AUTH_START_PATH = "/auth/start";
export const LOGOUT_PATH = "/logout";
export const ACCOUNT_PATH = "/api/account";
export const ACCOUNT_CONSOLE_PATH = "/account";
export const RETURN_TO_QUERY_PARAMETER = "next";
export const REPLAY_LOG_LIMIT = 256;
export const DESCRIPTION_TYPES: readonly SIGNAL_TYPE[] = [SIGNAL_TYPE.ANSWER, SIGNAL_TYPE.OFFER];
export const SUPERSEDED_BY_A_DESCRIPTION: readonly SIGNAL_TYPE[] = [
    SIGNAL_TYPE.ANSWER,
    SIGNAL_TYPE.ICE_CANDIDATE,
    SIGNAL_TYPE.OFFER,
];
