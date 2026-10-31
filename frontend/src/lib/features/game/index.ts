import type { FeatureCore } from "#lib/call/feature-port.ts";
import { featureView, type FeatureView } from "#lib/ui/chat.svelte.ts";
import type { ToastAction, ToastKind } from "#lib/ui/toast.svelte.ts";
import { AUTH_START_PATH, RETURN_TO_QUERY_PARAMETER, SIGNAL_TYPE } from "#lib/wire.ts";
import GameMasterLine from "./GameMasterLine.svelte";
import GameMasterThinking from "./GameMasterThinking.svelte";
import QuestionCard from "./QuestionCard.svelte";
import {
    Quiz,
    type AnswerCountFrame,
    type LineFrame,
    type PhaseFrame,
    type QuestionCardProps,
    type RefusedFrame,
} from "./quiz.svelte.ts";
import { GAME_REFUSAL, GAME_SIGNAL } from "./wire.ts";
import "./game.css";

const SPARK_PATHS =
    '<path d="M10.5 4l1.9 5.6 5.6 1.9-5.6 1.9-1.9 5.6-1.9-5.6L3 11.5l5.6-1.9z"/><path d="M18.5 3v4M16.5 5h4"/>';
const X_PATHS = '<path d="M6 6l12 12M18 6L6 18"/>';
const MORE_ITEMS_MARKUP = `<button id="gameStartBtn" class="mi game-item" type="button"><svg class="icon not-thinking not-ending" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPARK_PATHS}</svg><svg class="icon ending-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${X_PATHS}</svg><svg class="icon thinking-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5"/></svg><span class="grow start-only">Start quiz<small>The game master asks the call</small></span><span class="grow running-only"><span class="not-ending">Quiz running</span><span class="ending-only">End quiz</span><small id="gameProgress"></small></span><span class="grow thinking-only">Game master is thinking…</span></button>
<p class="quiet-note"><span>Game answers and names go to the AI service. Chat does not. <a href="https://hallucinai.de/datenschutz#hopin-spielleiter" target="_blank" rel="noopener">Datenschutz</a></span></p>`;

interface SeatFrame {
    participant_id: string;
    is_host: boolean;
}

interface EndedFrame {
    leaver?: string;
}

type RefusalToast = (
    frame: RefusedFrame,
    toastKind: FeatureCore["toastKind"],
) => [string, ToastKind, ToastAction | null];

function signInAgain() {
    const returnTo = `${location.pathname}${location.search}`;
    location.assign(`${AUTH_START_PATH}?${new URLSearchParams({ [RETURN_TO_QUERY_PARAMETER]: returnTo })}`);
}

function busyText(frame: RefusedFrame) {
    return frame.retry_after_seconds === undefined
        ? "The game master is busy."
        : `The game master is busy. Try again in ${String(frame.retry_after_seconds)} s.`;
}

const UNAVAILABLE_TOAST: RefusalToast = (_frame, kind) => [
    "The game master is unavailable right now.",
    kind.WARNING,
    null,
];

const REFUSAL_TOASTS: Partial<Record<GAME_REFUSAL, RefusalToast>> = {
    [GAME_REFUSAL.NO_ACCESS]: (_frame, kind) => ["Your account has no AI access.", kind.LOCKED, null],
    [GAME_REFUSAL.SIGN_IN_AGAIN]: (_frame, kind) => [
        "Sign in again to use the game master.",
        kind.WARNING,
        { label: "Sign in", onAct: signInAgain },
    ],
    [GAME_REFUSAL.BUSY]: (frame, kind) => [busyText(frame), kind.WAIT, null],
    [GAME_REFUSAL.ALREADY_ANSWERING]: (_frame, kind) => ["The game master is already answering.", kind.WAIT, null],
    [GAME_REFUSAL.QUIZ_RUNNING]: (_frame, kind) => ["A quiz is already running.", kind.WAIT, null],
    [GAME_REFUSAL.NOT_STARTER]: (_frame, kind) => [
        "Only the person who started the quiz can reveal.",
        kind.LOCKED,
        null,
    ],
    [GAME_REFUSAL.NOT_STARTER_OR_HOST]: (_frame, kind) => [
        "Only the person who started the quiz or the host can end it.",
        kind.LOCKED,
        null,
    ],
    [GAME_REFUSAL.ANSWER_CLOSED]: (_frame, kind) => ["This question is already closed.", kind.LOCKED, null],
    [GAME_REFUSAL.ANSWER_TOO_LONG]: (_frame, kind) => [
        "Your answer is too long. Shorten it and send again.",
        kind.WARNING,
        null,
    ],
    [GAME_REFUSAL.UNAVAILABLE]: UNAVAILABLE_TOAST,
};

function moreItems() {
    const template = document.createElement("template");
    template.innerHTML = MORE_ITEMS_MARKUP;
    const fragment = template.content;
    return {
        items: Array.from(fragment.children),
        startButton: fragment.querySelector("#gameStartBtn") as HTMLButtonElement,
        progress: fragment.querySelector("#gameProgress") as HTMLElement,
    };
}

function listen(core: FeatureCore, quiz: Quiz) {
    core.on(GAME_SIGNAL.GAME_PHASE, (frame: PhaseFrame) => {
        quiz.receivePhase(frame);
    });
    core.on(GAME_SIGNAL.GAME_LINE, (frame: LineFrame) => {
        core.receiveChatLine(featureView(GameMasterLine, { text: frame.text }));
        quiz.receiveLine(frame);
    });
    core.on(GAME_SIGNAL.GAME_ANSWER_COUNT, (frame: AnswerCountFrame) => {
        quiz.receiveAnswerCount(frame);
    });
    core.on(GAME_SIGNAL.GAME_REFUSED, (frame: RefusedFrame) => {
        quiz.receiveRefusal(frame);
        const toastOf = REFUSAL_TOASTS[frame.reason] ?? UNAVAILABLE_TOAST;
        core.showToast(...toastOf(frame, core.toastKind));
    });
    core.on(GAME_SIGNAL.GAME_ENDED, (frame: EndedFrame) => {
        const text = frame.leaver === undefined ? "Quiz ended." : `Quiz ended: ${frame.leaver} left.`;
        core.showToast(text, core.toastKind.FAILED, null);
    });
    const joined = (frame: SeatFrame) => {
        quiz.joined(frame.participant_id, frame.is_host);
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, joined);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, joined);
    core.onCallEnded(() => {
        quiz.reset();
    });
}

export function install(core: FeatureCore): Promise<void> {
    const more = moreItems();
    let thinkingView: FeatureView | null = null;
    let handedCard: QuestionCardProps | null = null;

    function renderMore() {
        more.startButton.classList.toggle("thinking", quiz.isThinking);
        more.startButton.classList.toggle("running", quiz.isRunning);
        more.startButton.classList.toggle("ending", quiz.isEnding);
        more.startButton.disabled = quiz.startDisabled;
        more.startButton.setAttribute("aria-busy", String(quiz.isThinking));
        more.progress.textContent = quiz.progress;
    }

    function showThinking() {
        if (quiz.isThinking === (thinkingView !== null)) return;
        thinkingView = quiz.isThinking ? featureView(GameMasterThinking, {}) : null;
        core.pinLastChatLine(thinkingView);
    }

    // The card's view is handed over once per round; its props change in place after that.
    function showCard() {
        const card = quiz.card;
        if (card === handedCard) return;
        handedCard = card;
        core.showChatCard(card === null ? null : featureView(QuestionCard, card));
    }

    const quiz: Quiz = new Quiz(core, () => {
        renderMore();
        showThinking();
        showCard();
    });

    more.startButton.addEventListener("click", () => {
        quiz.startOrEnd();
    });
    listen(core, quiz);
    core.addMoreItems(...more.items);
    return Promise.resolve();
}
