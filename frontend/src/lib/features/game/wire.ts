// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.

export enum GAME_SIGNAL {
    GAME_START = "game_start",
    GAME_REVEAL = "game_reveal",
    GAME_END = "game_end",
    GAME_ANSWER = "game_answer",
    GAME_ANSWER_COUNT = "game_answer_count",
    GAME_PHASE = "game_phase",
    GAME_LINE = "game_line",
    GAME_REFUSED = "game_refused",
    GAME_ENDED = "game_ended",
}

export enum GAME_PHASE {
    IDLE = "idle",
    THINKING = "thinking",
    ANSWERING = "answering",
}

export enum GAME_REFUSAL {
    NO_ACCESS = "no_access",
    SIGN_IN_AGAIN = "sign_in_again",
    BUSY = "busy",
    ALREADY_ANSWERING = "already_answering",
    QUIZ_RUNNING = "quiz_running",
    NOT_STARTER = "not_starter",
    NOT_STARTER_OR_HOST = "not_starter_or_host",
    ANSWER_CLOSED = "answer_closed",
    ANSWER_TOO_LONG = "answer_too_long",
    UNAVAILABLE = "unavailable",
}
