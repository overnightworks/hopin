import { AUTH_START_PATH, GAME_PHASE, GAME_REFUSAL, RETURN_TO_QUERY_PARAMETER, SIGNAL_TYPE } from "../js/wire.js";

const SPARK_PATHS = '<path d="M10.5 4l1.9 5.6 5.6 1.9-5.6 1.9-1.9 5.6-1.9-5.6L3 11.5l5.6-1.9z"/><path d="M18.5 3v4M16.5 5h4"/>';
const X_PATHS = '<path d="M6 6l12 12M18 6L6 18"/>';
const MORE_ITEMS_MARKUP = `<button id="gameStartBtn" class="mi game-item" type="button"><svg class="icon not-thinking not-ending" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPARK_PATHS}</svg><svg class="icon ending-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${X_PATHS}</svg><svg class="icon thinking-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5"/></svg><span class="grow start-only">Start quiz<small>The game master asks the call</small></span><span class="grow running-only"><span class="not-ending">Quiz running</span><span class="ending-only">End quiz</span><small id="gameProgress"></small></span><span class="grow thinking-only">Game master is thinking…</span></button>
<p class="quiet-note"><span>Game answers and names go to the AI service. Chat does not. <a href="https://hallucinai.de/datenschutz#hopin-spielleiter" target="_blank" rel="noopener">Datenschutz</a></span></p>`;
const CARD_MARKUP = `<section class="qcard" aria-label="Quiz question"><div class="qcard-head"><span class="qcard-tag"><svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPARK_PATHS}</svg></span><button type="button" class="qcard-reveal">Reveal</button></div><p class="qcard-q"></p><form class="qcard-form"><div class="qcard-row"><input class="qcard-input" type="text" autocomplete="off" placeholder="Your answer" aria-label="Your answer"><button type="submit" class="btn btn--primary qcard-send">Send</button></div><p class="qcard-status" aria-live="polite"></p></form></section>`;
const SENDER_MARKUP = `<div class="chat-sender game-master-sender"><svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPARK_PATHS}</svg>Game master<span class="ai-badge">AI</span></div>`;

let core = null;
let startButton = null;
let progress = null;
let sender = null;
let thinkingBubble = null;
let ownParticipantId = null;
let isHost = false;
let phase = GAME_PHASE.IDLE;
let quiz = null;
let question = null;
let cardRound = null;
let revealButton = null;
let awaitingAnswer = false;
let answerForm = null;
let answerCount = null;
let answerSent = false;
let answerSentBeforeLastSend = false;

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
    [GAME_REFUSAL.QUIZ_RUNNING]: () => ["A quiz is already running.", core.toastKind.WAIT, null],
    [GAME_REFUSAL.NOT_STARTER]: () => ["Only the person who started the quiz can reveal.", core.toastKind.LOCKED, null],
    [GAME_REFUSAL.NOT_STARTER_OR_HOST]: () => [
        "Only the person who started the quiz or the host can end it.",
        core.toastKind.LOCKED,
        null,
    ],
    [GAME_REFUSAL.ANSWER_CLOSED]: () => ["This question is already closed.", core.toastKind.LOCKED, null],
    [GAME_REFUSAL.ANSWER_TOO_LONG]: () => [
        "Your answer is too long. Shorten it and send again.",
        core.toastKind.WARNING,
        null,
    ],
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

function cardElement() {
    const card = fromMarkup(CARD_MARKUP).firstElementChild;
    card.querySelector(".qcard-tag").append(`Question ${quiz.round} of ${quiz.rounds}`);
    card.querySelector(".qcard-q").textContent = question.text;
    const reveal = card.querySelector(".qcard-reveal");
    if (quiz.starter === ownParticipantId) {
        reveal.addEventListener("click", revealAnswer);
        revealButton = reveal;
    } else {
        reveal.remove();
    }
    answerForm = card.querySelector(".qcard-form");
    answerForm.addEventListener("submit", sendAnswer);
    answerCount = null;
    answerSent = false;
    answerSentBeforeLastSend = false;
    return card;
}

function sendAnswer(event) {
    event.preventDefault();
    const text = answerForm.querySelector(".qcard-input").value.trim();
    if (text === "" || !core.isConnected()) return;
    core.send({ type: SIGNAL_TYPE.GAME_ANSWER, text });
    answerSentBeforeLastSend = answerSent;
    answerSent = true;
    renderCard();
}

function answerStatusText() {
    if (answerCount === null) return "";
    const count = `${answerCount.answered} of ${answerCount.players} answered`;
    return answerSent ? `Answer sent · ${count}` : count;
}

function renderAnswerForm() {
    const locked = phase !== GAME_PHASE.ANSWERING || awaitingAnswer;
    answerForm.querySelector(".qcard-input").disabled = locked;
    answerForm.querySelector(".qcard-send").disabled = locked;
    const status = answerForm.querySelector(".qcard-status");
    status.textContent = answerStatusText();
    status.classList.toggle("sent", answerSent);
}

function renderCard() {
    // A page shows a round's card only once it holds that round's question; a seat seated later waits for the next.
    const round = quiz !== null && question?.round === quiz.round ? quiz.round : null;
    if (round !== cardRound) {
        cardRound = round;
        revealButton = null;
        answerForm = null;
        core.showChatCard(round === null ? null : cardElement());
    }
    if (answerForm !== null) renderAnswerForm();
    if (revealButton !== null) revealButton.disabled = phase !== GAME_PHASE.ANSWERING || awaitingAnswer;
}

function mayEndQuiz() {
    return quiz !== null && (quiz.starter === ownParticipantId || isHost);
}

function render() {
    const isThinking = phase === GAME_PHASE.THINKING;
    const isRunning = phase === GAME_PHASE.ANSWERING;
    const isEnding = isRunning && mayEndQuiz();
    startButton.classList.toggle("thinking", isThinking);
    startButton.classList.toggle("running", isRunning);
    startButton.classList.toggle("ending", isEnding);
    startButton.disabled = isThinking || (isRunning && !isEnding) || awaitingAnswer;
    startButton.setAttribute("aria-busy", String(isThinking));
    progress.textContent = quiz === null ? "" : `Question ${quiz.round} of ${quiz.rounds}`;
    showThinking(isThinking);
    renderCard();
}

function askGameMaster(signalType) {
    if (!core.isConnected()) return;
    awaitingAnswer = true;
    render();
    core.send({ type: signalType });
}

function startOrEndQuiz() {
    askGameMaster(phase === GAME_PHASE.IDLE ? SIGNAL_TYPE.GAME_START : SIGNAL_TYPE.GAME_END);
}

function revealAnswer() {
    askGameMaster(SIGNAL_TYPE.GAME_REVEAL);
}

function receivePhase(frame) {
    phase = frame.phase;
    awaitingAnswer = false;
    if (phase === GAME_PHASE.IDLE) {
        quiz = null;
        question = null;
    } else {
        quiz = { round: frame.round, rounds: frame.rounds, starter: frame.starter };
    }
    render();
}

function receiveLineFrame(frame) {
    receiveLine(frame.text);
    // A line without a round is the reveal or the final score: the card shows only an open question.
    question = Number.isInteger(frame.round) ? { round: frame.round, text: frame.text } : null;
    render();
}

function receiveEnded() {
    core.showToast("Quiz ended.", core.toastKind.FAILED, null);
}

function receiveAnswerCount(frame) {
    answerCount = { answered: frame.answered, players: frame.players };
    render();
}

function showRefusal(frame) {
    awaitingAnswer = false;
    if (frame.reason === GAME_REFUSAL.ANSWER_CLOSED) answerSent = false;
    if (frame.reason === GAME_REFUSAL.ANSWER_TOO_LONG) answerSent = answerSentBeforeLastSend;
    render();
    const toastOf = REFUSAL_TOAST[frame.reason] ?? REFUSAL_TOAST[GAME_REFUSAL.UNAVAILABLE];
    core.showToast(...toastOf(frame));
}

function reset() {
    phase = GAME_PHASE.IDLE;
    quiz = null;
    question = null;
    awaitingAnswer = false;
    render();
}

function joined(frame) {
    ownParticipantId = frame.participant_id;
    isHost = frame.is_host;
    reset();
}

export async function install(callCore) {
    await loadStylesheet();
    core = callCore;
    sender = fromMarkup(SENDER_MARKUP).firstElementChild;
    const moreItems = fromMarkup(MORE_ITEMS_MARKUP);
    startButton = moreItems.getElementById("gameStartBtn");
    progress = moreItems.getElementById("gameProgress");
    startButton.addEventListener("click", startOrEndQuiz);
    core.on(SIGNAL_TYPE.GAME_PHASE, receivePhase);
    core.on(SIGNAL_TYPE.GAME_LINE, receiveLineFrame);
    core.on(SIGNAL_TYPE.GAME_ANSWER_COUNT, receiveAnswerCount);
    core.on(SIGNAL_TYPE.GAME_REFUSED, showRefusal);
    core.on(SIGNAL_TYPE.GAME_ENDED, receiveEnded);
    core.on(SIGNAL_TYPE.ROOM_CREATED, joined);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, joined);
    core.onCallEnded(reset);
    core.addMoreItems(...moreItems.children);
}
