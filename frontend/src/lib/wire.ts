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

export const SESSION_ENDED_CLOSE_CODE = 4002;
export const WEBSOCKET_PATH = "/ws";
export const REPLAY_LOG_LIMIT = 256;
export const DESCRIPTION_TYPES: readonly SIGNAL_TYPE[] = [SIGNAL_TYPE.ANSWER, SIGNAL_TYPE.OFFER];
export const SUPERSEDED_BY_A_DESCRIPTION: readonly SIGNAL_TYPE[] = [
    SIGNAL_TYPE.ANSWER,
    SIGNAL_TYPE.ICE_CANDIDATE,
    SIGNAL_TYPE.OFFER,
];
