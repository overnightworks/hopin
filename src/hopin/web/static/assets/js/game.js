import { receiveGameMasterLine, showGameMasterThinking } from "./chat.js";
import { SIGNAL_TYPE, isConnected, on, send } from "./signaling.js";
import { TOAST_KIND, showToast } from "./toast.js";
import { AUTH_START_PATH, GAME_PHASE, GAME_REFUSAL, RETURN_TO_QUERY_PARAMETER } from "./wire.js";

const startButton = document.getElementById("gameStartBtn");

let phase = GAME_PHASE.IDLE;
let awaitingAnswer = false;

function signInAgain() {
    const returnTo = `${location.pathname}${location.search}`;
    location.assign(`${AUTH_START_PATH}?${new URLSearchParams({ [RETURN_TO_QUERY_PARAMETER]: returnTo })}`);
}

function busyText(frame) {
    return Number.isInteger(frame.retry_after_seconds)
        ? `The game master is busy. Try again in ${frame.retry_after_seconds} s.`
        : "The game master is busy.";
}

const REFUSAL_TOAST = {
    [GAME_REFUSAL.NO_ACCESS]: () => ["Your account has no AI access.", TOAST_KIND.LOCKED, null],
    [GAME_REFUSAL.SIGN_IN_AGAIN]: () => [
        "Sign in again to use the game master.",
        TOAST_KIND.WARNING,
        { label: "Sign in", onAct: signInAgain },
    ],
    [GAME_REFUSAL.BUSY]: (frame) => [busyText(frame), TOAST_KIND.WAIT, null],
    [GAME_REFUSAL.ALREADY_ANSWERING]: () => ["The game master is already answering.", TOAST_KIND.WAIT, null],
    [GAME_REFUSAL.UNAVAILABLE]: () => ["The game master is unavailable right now.", TOAST_KIND.WARNING, null],
};

function render() {
    const isThinking = phase === GAME_PHASE.THINKING;
    startButton.classList.toggle("thinking", isThinking);
    startButton.disabled = isThinking || awaitingAnswer;
    startButton.setAttribute("aria-busy", String(isThinking));
    showGameMasterThinking(isThinking);
}

function startQuiz() {
    if (!isConnected()) return;
    awaitingAnswer = true;
    render();
    send({ type: SIGNAL_TYPE.GAME_START });
}

function showRefusal(frame) {
    awaitingAnswer = false;
    render();
    const toastOf = REFUSAL_TOAST[frame.reason] ?? REFUSAL_TOAST[GAME_REFUSAL.UNAVAILABLE];
    showToast(...toastOf(frame));
}

export function resetGame() {
    phase = GAME_PHASE.IDLE;
    awaitingAnswer = false;
    render();
}

export function registerGameHandlers() {
    on(SIGNAL_TYPE.GAME_PHASE, (frame) => {
        phase = frame.phase;
        awaitingAnswer = false;
        render();
    });
    on(SIGNAL_TYPE.GAME_LINE, (frame) => receiveGameMasterLine(frame.text));
    on(SIGNAL_TYPE.GAME_REFUSED, showRefusal);
    on(SIGNAL_TYPE.ROOM_CREATED, resetGame);
    on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, resetGame);
}

export function initGame() {
    startButton.addEventListener("click", startQuiz);
}
