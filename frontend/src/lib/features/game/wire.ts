// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.

export enum GAME_SIGNAL {
    GAME_START = "game_start",
    GAME_REVEAL = "game_reveal",
    GAME_END = "game_end",
    GAME_ANSWER = "game_answer",
    GAME_ANSWER_COUNT = "game_answer_count",
    GAME_PHASE = "game_phase",
    GAME_LINE = "game_line",
    GAME_VOICE = "game_voice",
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
    BUDGET_SPENT = "budget_spent",
    ALREADY_ANSWERING = "already_answering",
    QUIZ_RUNNING = "quiz_running",
    NOT_STARTER = "not_starter",
    NOT_STARTER_OR_HOST = "not_starter_or_host",
    ANSWER_CLOSED = "answer_closed",
    ANSWER_TOO_LONG = "answer_too_long",
    UNAVAILABLE = "unavailable",
}

export type IdlePhaseFrame = {
    type: GAME_SIGNAL.GAME_PHASE;
    phase: GAME_PHASE.IDLE;
};

export type QuizPhaseFrame = {
    type: GAME_SIGNAL.GAME_PHASE;
    phase: GAME_PHASE.THINKING | GAME_PHASE.ANSWERING;
    round: number;
    rounds: number;
    starter: string;
};

export type LineFrame = {
    type: GAME_SIGNAL.GAME_LINE;
    text: string;
    voiced?: true;
    round?: number;
};

export type VoiceFrame = {
    type: GAME_SIGNAL.GAME_VOICE;
    id: string;
    path: string;
};

export type SilentVoiceFrame = {
    type: GAME_SIGNAL.GAME_VOICE;
};

export type AnswerCountFrame = {
    type: GAME_SIGNAL.GAME_ANSWER_COUNT;
    answered: number;
    players: number;
    own_answer_counted?: true;
};

export type BusyRefusedFrame = {
    type: GAME_SIGNAL.GAME_REFUSED;
    reason: GAME_REFUSAL.BUSY;
    retry_after_seconds: number;
};

export type RefusedFrame = {
    type: GAME_SIGNAL.GAME_REFUSED;
    reason:
        | GAME_REFUSAL.NO_ACCESS
        | GAME_REFUSAL.SIGN_IN_AGAIN
        | GAME_REFUSAL.BUDGET_SPENT
        | GAME_REFUSAL.ALREADY_ANSWERING
        | GAME_REFUSAL.QUIZ_RUNNING
        | GAME_REFUSAL.NOT_STARTER
        | GAME_REFUSAL.NOT_STARTER_OR_HOST
        | GAME_REFUSAL.ANSWER_CLOSED
        | GAME_REFUSAL.ANSWER_TOO_LONG
        | GAME_REFUSAL.UNAVAILABLE;
};

export type EndedFrame = {
    type: GAME_SIGNAL.GAME_ENDED;
    leaver?: string;
};

export type GameFrameByType = {
    [GAME_SIGNAL.GAME_PHASE]: IdlePhaseFrame | QuizPhaseFrame;
    [GAME_SIGNAL.GAME_LINE]: LineFrame;
    [GAME_SIGNAL.GAME_VOICE]: VoiceFrame | SilentVoiceFrame;
    [GAME_SIGNAL.GAME_ANSWER_COUNT]: AnswerCountFrame;
    [GAME_SIGNAL.GAME_REFUSED]: BusyRefusedFrame | RefusedFrame;
    [GAME_SIGNAL.GAME_ENDED]: EndedFrame;
};
