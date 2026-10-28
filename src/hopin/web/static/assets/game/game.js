import { AUTH_START_PATH, GAME_PHASE, GAME_REFUSAL, RETURN_TO_QUERY_PARAMETER, SIGNAL_TYPE } from "../js/wire.js";

const SPARK_PATHS = '<path d="M10.5 4l1.9 5.6 5.6 1.9-5.6 1.9-1.9 5.6-1.9-5.6L3 11.5l5.6-1.9z"/><path d="M18.5 3v4M16.5 5h4"/>';
const MORE_ITEMS_MARKUP = `<button id="gameStartBtn" class="mi game-item" type="button"><svg class="icon idle-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPARK_PATHS}</svg><svg class="icon thinking-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5"/></svg><span class="grow idle-only">Start quiz<small>The game master asks the call</small></span><span class="grow thinking-only">Game master is thinking…</span></button>
<p class="quiet-note"><span>Quiz questions come from an AI service. Chat does not go there. <a href="https://hallucinai.de/datenschutz#hopin-spielleiter" target="_blank" rel="noopener">Datenschutz</a></span></p>`;
const SENDER_MARKUP = `<div class="chat-sender game-master-sender"><svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPARK_PATHS}</svg>Game master<span class="ai-badge">AI</span></div>`;

let core = null;
let startButton = null;
let sender = null;
let thinkingBubble = null;
let phase = GAME_PHASE.IDLE;
let awaitingAnswer = false;

function fromMarkup(markup) {
    const template = document.createElement("template");
    template.innerHTML = markup;
    return template.content;
}

function loadStylesheet() {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = new URL("./game.css", import.meta.url).pathname;
    const loaded = new Promise((resolve, reject) => {
        link.addEventListener("load", resolve);
        link.addEventListener("error", () => reject(new Error(`The game's stylesheet ${link.href} did not load`)));
    });
    document.head.append(link);
    return loaded;
}

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
    [GAME_REFUSAL.NO_ACCESS]: () => ["Your account has no AI access.", core.toastKind.LOCKED, null],
    [GAME_REFUSAL.SIGN_IN_AGAIN]: () => [
        "Sign in again to use the game master.",
        core.toastKind.WARNING,
        { label: "Sign in", onAct: signInAgain },
    ],
    [GAME_REFUSAL.BUSY]: (frame) => [busyText(frame), core.toastKind.WAIT, null],
    [GAME_REFUSAL.ALREADY_ANSWERING]: () => ["The game master is already answering.", core.toastKind.WAIT, null],
    [GAME_REFUSAL.UNAVAILABLE]: () => ["The game master is unavailable right now.", core.toastKind.WARNING, null],
};

function gameMasterBubble() {
    const messageElement = document.createElement("div");
    messageElement.className = "chat-msg other game-master";
    messageElement.append(sender.cloneNode(true));
    return messageElement;
}

function receiveLine(text) {
    const messageElement = gameMasterBubble();
    messageElement.append(text);
    core.receiveChatLine(messageElement);
}

function thinkingBubbleElement() {
    const bubble = gameMasterBubble();
    bubble.classList.add("typing");
    bubble.setAttribute("role", "status");
    bubble.setAttribute("aria-label", "Game master is thinking");
    const dots = document.createElement("p");
    dots.append(...Array.from({ length: 3 }, () => document.createElement("i")));
    bubble.append(dots);
    return bubble;
}

function showThinking(isThinking) {
    if (isThinking === (thinkingBubble !== null)) return;
    thinkingBubble = isThinking ? thinkingBubbleElement() : null;
    core.pinLastChatLine(thinkingBubble);
}

function render() {
    const isThinking = phase === GAME_PHASE.THINKING;
    startButton.classList.toggle("thinking", isThinking);
    startButton.disabled = isThinking || awaitingAnswer;
    startButton.setAttribute("aria-busy", String(isThinking));
    showThinking(isThinking);
}

function startQuiz() {
    if (!core.isConnected()) return;
    awaitingAnswer = true;
    render();
    core.send({ type: SIGNAL_TYPE.GAME_START });
}

function showRefusal(frame) {
    awaitingAnswer = false;
    render();
    const toastOf = REFUSAL_TOAST[frame.reason] ?? REFUSAL_TOAST[GAME_REFUSAL.UNAVAILABLE];
    core.showToast(...toastOf(frame));
}

function reset() {
    phase = GAME_PHASE.IDLE;
    awaitingAnswer = false;
    render();
}

export async function install(callCore) {
    await loadStylesheet();
    core = callCore;
    sender = fromMarkup(SENDER_MARKUP).firstElementChild;
    const moreItems = fromMarkup(MORE_ITEMS_MARKUP);
    startButton = moreItems.getElementById("gameStartBtn");
    startButton.addEventListener("click", startQuiz);
    core.on(SIGNAL_TYPE.GAME_PHASE, (frame) => {
        phase = frame.phase;
        awaitingAnswer = false;
        render();
    });
    core.on(SIGNAL_TYPE.GAME_LINE, (frame) => receiveLine(frame.text));
    core.on(SIGNAL_TYPE.GAME_REFUSED, showRefusal);
    core.on(SIGNAL_TYPE.ROOM_CREATED, reset);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, reset);
    core.onCallEnded(reset);
    core.addMoreItems(...moreItems.children);
}
