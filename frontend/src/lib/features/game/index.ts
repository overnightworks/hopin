import { createPlayer as createSpeechPlayer, type Player } from "overnightworks-speech-browser";
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

// A switch narrows the frame by its reason, so the busy wait is read without a cast; a keyed table cannot.
const refusalToast: RefusalToast = (frame, kind) => {
    switch (frame.reason) {
        case GAME_REFUSAL.NO_ACCESS:
            return [words["game.noAccessToast"], kind.LOCKED, null];
        case GAME_REFUSAL.SIGN_IN_AGAIN:
            return [
                words["game.signInAgainToast"](MASTER_NAME),
                kind.WARNING,
                { label: words["game.signInButton"], onAct: signInAgain },
            ];
        case GAME_REFUSAL.BUSY:
            return [words["game.busyRetryToast"](frame.retry_after_seconds, MASTER_NAME), kind.WAIT, null];
        case GAME_REFUSAL.BUDGET_SPENT:
            return [words["game.budgetSpentToast"], kind.LOCKED, null];
        case GAME_REFUSAL.ALREADY_ANSWERING:
            return [words["game.alreadyAnsweringToast"](MASTER_NAME), kind.WAIT, null];
        case GAME_REFUSAL.QUIZ_RUNNING:
            return [words["game.quizRunningToast"], kind.WAIT, null];
        case GAME_REFUSAL.NOT_STARTER:
            return [words["game.notStarterToast"], kind.LOCKED, null];
        case GAME_REFUSAL.NOT_STARTER_OR_HOST:
            return [words["game.notStarterOrHostToast"], kind.LOCKED, null];
        case GAME_REFUSAL.ANSWER_CLOSED:
            return [words["game.answerClosedToast"], kind.LOCKED, null];
        case GAME_REFUSAL.ANSWER_TOO_LONG:
            return [words["game.answerTooLongToast"], kind.WARNING, null];
        case GAME_REFUSAL.UNAVAILABLE:
        default:
            return [words["game.unavailableToast"](MASTER_NAME), kind.WARNING, null];
    }
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

/** The game master's voice for the person's browser: voices play one after another through one speech player, which fetches the route's body and ends each voice exactly once, played or not; a voice that cannot be fetched or played leaves its line as text. The player is made with the first voice. */
function createVoice(createPlayer: () => Player, quiz: Quiz) {
    let player: Player | null = null;

    function play(voiceId: string, path: string) {
        player ??= createPlayer();
        player.setMuted(quiz.voiceMuted);
        const playback = player.play(path);
        void playback.started.then((start) => {
            if (start.kind === "playing") quiz.voiceStarted(voiceId);
        });
        void playback.ended.then(() => {
            quiz.voiceEnded(voiceId);
        });
    }

    return {
        play,
        stop: () => player?.stop(),
        // A muted player ends every voice muted and fetches nothing; the choice follows the quiz, which a new call resets.
        syncMuted: () => player?.setMuted(quiz.voiceMuted),
    };
}

function listen(core: FeatureCore, quiz: Quiz, createPlayer: () => Player) {
    const voice = createVoice(createPlayer, quiz);
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
            voice.play(frame.id, frame.path);
        }
        quiz.voiceFrameArrived();
    });
    core.on(GAME_SIGNAL.GAME_ANSWER_COUNT, (frame: AnswerCountFrame) => {
        quiz.receiveAnswerCount(frame);
    });
    core.on(GAME_SIGNAL.GAME_REFUSED, (frame: RefusedFrame) => {
        quiz.receiveRefusal(frame);
        core.showToast(...refusalToast(frame, core.toastKind));
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
        voice.syncMuted();
    });
    return voice;
}

export function install(core: FeatureCore): Promise<void> {
    return installWith(core, createSpeechPlayer);
}

/** `createPlayer` makes the player of the game master's voice. */
export function installWith(core: FeatureCore, createPlayer: () => Player): Promise<void> {
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
    const voice = listen(core, quiz, createPlayer);
    more.voiceButton.addEventListener("click", () => {
        quiz.toggleVoiceMuted();
        voice.syncMuted();
    });
    core.addMoreItems(...more.items);
    return Promise.resolve();
}
