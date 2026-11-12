import type { AgentTile, FeatureCore } from "#lib/call/feature-port.ts";
import { featureView, type FeatureView } from "#lib/ui/chat.svelte.ts";
import type { ToastAction, ToastKind } from "#lib/ui/toast.svelte.ts";
import { words } from "#lib/words/de.ts";
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
    type VoiceFrame,
} from "./quiz.svelte.ts";
import { GAME_REFUSAL, GAME_SIGNAL } from "./wire.ts";
import "./game.css";

const SPARK_PATHS =
    '<path d="M10.5 4l1.9 5.6 5.6 1.9-5.6 1.9-1.9 5.6-1.9-5.6L3 11.5l5.6-1.9z"/><path d="M18.5 3v4M16.5 5h4"/>';
const X_PATHS = '<path d="M6 6l12 12M18 6L6 18"/>';
const VOICE_ON_PATHS =
    '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>';
const VOICE_OFF_PATHS = '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>';
const MASTER_NAME = { name: words["game.masterName"] };
const MORE_ITEMS_MARKUP = `<button id="gameStartBtn" class="mi game-item" type="button"><svg class="icon not-thinking not-ending" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPARK_PATHS}</svg><svg class="icon ending-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${X_PATHS}</svg><svg class="icon thinking-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5"/></svg><span class="grow start-only">${words["game.startButton"]}<small>${words["game.startHint"](MASTER_NAME)}</small></span><span class="grow running-only"><span class="not-ending">${words["game.runningLabel"]}</span><span class="ending-only">${words["game.endButton"]}</span><small id="gameProgress"></small></span><span class="grow thinking-only">${words["game.thinkingButton"](MASTER_NAME)}</span></button>
<p class="quiet-note"><span>Game answers and names go to the AI service. Chat does not. <a href="https://hallucinai.de/datenschutz#hopin-spielleiter" target="_blank" rel="noopener">Datenschutz</a></span></p>
<button id="gameVoiceBtn" class="mi game-item" type="button" aria-label="${words["game.voiceLabel"](MASTER_NAME)}" hidden><svg class="icon voice-on-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${VOICE_ON_PATHS}</svg><svg class="icon voice-off-only" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${VOICE_OFF_PATHS}</svg><span class="grow">${words["game.voiceLabel"](MASTER_NAME)}<small id="gameVoiceState"></small></span></button>`;

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
        ? words["game.busyToast"](MASTER_NAME)
        : words["game.busyRetryToast"](frame.retry_after_seconds, MASTER_NAME);
}

const UNAVAILABLE_TOAST: RefusalToast = (_frame, kind) => [
    words["game.unavailableToast"](MASTER_NAME),
    kind.WARNING,
    null,
];

const REFUSAL_TOASTS: Partial<Record<GAME_REFUSAL, RefusalToast>> = {
    [GAME_REFUSAL.NO_ACCESS]: (_frame, kind) => [words["game.noAccessToast"], kind.LOCKED, null],
    [GAME_REFUSAL.SIGN_IN_AGAIN]: (_frame, kind) => [
        words["game.signInAgainToast"](MASTER_NAME),
        kind.WARNING,
        { label: words["game.signInButton"], onAct: signInAgain },
    ],
    [GAME_REFUSAL.BUSY]: (frame, kind) => [busyText(frame), kind.WAIT, null],
    [GAME_REFUSAL.BUDGET_SPENT]: (_frame, kind) => [words["game.budgetSpentToast"], kind.LOCKED, null],
    [GAME_REFUSAL.ALREADY_ANSWERING]: (_frame, kind) => [
        words["game.alreadyAnsweringToast"](MASTER_NAME),
        kind.WAIT,
        null,
    ],
    [GAME_REFUSAL.QUIZ_RUNNING]: (_frame, kind) => [words["game.quizRunningToast"], kind.WAIT, null],
    [GAME_REFUSAL.NOT_STARTER]: (_frame, kind) => [words["game.notStarterToast"], kind.LOCKED, null],
    [GAME_REFUSAL.NOT_STARTER_OR_HOST]: (_frame, kind) => [words["game.notStarterOrHostToast"], kind.LOCKED, null],
    [GAME_REFUSAL.ANSWER_CLOSED]: (_frame, kind) => [words["game.answerClosedToast"], kind.LOCKED, null],
    [GAME_REFUSAL.ANSWER_TOO_LONG]: (_frame, kind) => [words["game.answerTooLongToast"], kind.WARNING, null],
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
        voiceButton: fragment.querySelector("#gameVoiceBtn") as HTMLButtonElement,
        voiceState: fragment.querySelector("#gameVoiceState") as HTMLElement,
    };
}

interface VoiceEvents {
    isMuted(): boolean;
    started(voiceId: string): void;
    ended(voiceId: string): void;
}

/** Plays one voice at a time, none while muted (a skipped voice ends like one that failed to load): the voice whose fetch finishes last replaces the playing one, and a stop drops every voice still being fetched; a voice that cannot be fetched or played leaves its line as text. Every voice it takes reports its end exactly once, played or not. */
function createVoicePlayer(events: VoiceEvents) {
    let releaseCurrent: (() => void) | null = null;
    let requested = 0;
    let stoppedThrough = 0;

    function stop() {
        stoppedThrough = requested;
        releaseCurrent?.();
        releaseCurrent = null;
    }

    function begin(blob: Blob, sequence: number, voiceId: string) {
        if (sequence <= stoppedThrough) return false;
        releaseCurrent?.();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        const release = () => {
            audio.pause();
            URL.revokeObjectURL(url);
            events.ended(voiceId);
        };
        releaseCurrent = release;
        audio.addEventListener("ended", release);
        void audio.play().then(() => {
            events.started(voiceId);
        }, release);
        return true;
    }

    async function play(voiceId: string, path: string) {
        if (events.isMuted()) {
            events.ended(voiceId);
            return;
        }
        requested += 1;
        const sequence = requested;
        try {
            const response = await fetch(path);
            if (response.ok && begin(await response.blob(), sequence, voiceId)) return;
        } catch {
            // The line stays text.
        }
        events.ended(voiceId);
    }

    return { play, stop };
}

function listen(core: FeatureCore, quiz: Quiz) {
    const voice = createVoicePlayer({
        isMuted: () => quiz.voiceMuted,
        started: (voiceId) => {
            quiz.voiceStarted(voiceId);
        },
        ended: (voiceId) => {
            quiz.voiceEnded(voiceId);
        },
    });
    core.on(GAME_SIGNAL.GAME_PHASE, (frame: PhaseFrame) => {
        quiz.receivePhase(frame);
    });
    core.on(GAME_SIGNAL.GAME_LINE, (frame: LineFrame) => {
        core.receiveChatLine(featureView(GameMasterLine, { text: frame.text }));
        quiz.receiveLine(frame);
    });
    core.on(GAME_SIGNAL.GAME_VOICE, (frame: VoiceFrame) => {
        if (frame.id !== undefined && quiz.isFirstHearing(frame.id)) {
            quiz.voiceRequested(frame.id);
            void voice.play(frame.id, frame.path);
        }
        quiz.voiceFrameArrived();
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
        const text =
            frame.leaver === undefined
                ? words["game.endedToast"]
                : words["game.endedByLeaverToast"]({ leaver: frame.leaver });
        core.showToast(text, core.toastKind.FAILED, null);
        voice.stop();
        quiz.receiveEnd();
    });
    const joined = (frame: SeatFrame) => {
        quiz.joined(frame.participant_id, frame.is_host);
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, joined);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, joined);
    core.onCallEnded(() => {
        voice.stop();
        quiz.reset();
    });
    return voice.stop;
}

export function install(core: FeatureCore): Promise<void> {
    const more = moreItems();
    let thinkingView: FeatureView | null = null;
    let handedCard: QuestionCardProps | null = null;
    let handedTile: AgentTile | null = null;

    function renderMore() {
        more.startButton.classList.toggle("thinking", quiz.isThinking);
        more.startButton.classList.toggle("running", quiz.isRunning);
        more.startButton.classList.toggle("ending", quiz.isEnding);
        more.startButton.disabled = quiz.startDisabled;
        more.startButton.setAttribute("aria-busy", String(quiz.isThinking));
        more.progress.textContent = quiz.progress;
        more.voiceButton.hidden = !quiz.voiceToggleShown;
        more.voiceButton.setAttribute("aria-pressed", String(!quiz.voiceMuted));
        more.voiceButton.classList.toggle("muted", quiz.voiceMuted);
        more.voiceState.textContent = quiz.voiceMuted ? words["game.voiceOff"] : words["game.voiceOn"];
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

    function showAgentTile() {
        const tile = quiz.agentTile;
        if (tile?.activity === handedTile?.activity && tile?.name === handedTile?.name) return;
        handedTile = tile;
        core.showAgentTile(tile);
    }

    const quiz: Quiz = new Quiz(core, () => {
        renderMore();
        showThinking();
        showCard();
        showAgentTile();
    });

    more.startButton.addEventListener("click", () => {
        quiz.startOrEnd();
    });
    const stopVoice = listen(core, quiz);
    more.voiceButton.addEventListener("click", () => {
        quiz.toggleVoiceMuted();
        if (quiz.voiceMuted) stopVoice();
    });
    core.addMoreItems(...more.items);
    return Promise.resolve();
}
