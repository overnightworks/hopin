// @vitest-environment jsdom
import { afterEach, describe, expect, it, onTestFinished, vi } from "vitest";
import { AGENT_ACTIVITY, type AgentTile, type FeatureCore } from "#lib/call/feature-port.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import { TOAST_KIND, type ToastAction, type ToastKind } from "#lib/ui/toast.svelte.ts";
import { AUTH_START_PATH, RETURN_TO_QUERY_PARAMETER, SIGNAL_TYPE } from "#lib/wire.ts";
import { words } from "#lib/words/de.ts";
import GameMasterLine from "./GameMasterLine.svelte";
import GameMasterThinking from "./GameMasterThinking.svelte";
import { install } from "./index.ts";
import QuestionCard from "./QuestionCard.svelte";
import type { QuestionCardProps } from "./quiz.svelte.ts";
import { GAME_PHASE, GAME_REFUSAL, GAME_SIGNAL } from "./wire.ts";

// Core builds a view from a component and its props; the cases read the pair back to see what was handed over.
const views = vi.hoisted(() => new WeakMap<object, { component: unknown; props: unknown }>());
vi.mock("#lib/ui/chat.svelte.ts", () => ({
    featureView: (component: unknown, props: unknown) => {
        const view = () => ({});
        views.set(view, { component, props });
        return view;
    },
}));

const ME = "me";
const master = { name: words["game.masterName"] };
const roundOfThree = (round: number) => words["game.questionProgress"]({ round: String(round), rounds: "3" });

interface HandedOver {
    component: unknown;
    props: unknown;
}

function handedOver(view: FeatureView | null): HandedOver | null {
    return view === null ? null : (views.get(view) ?? null);
}

interface Toast {
    text: string;
    kind: ToastKind;
    action: ToastAction | null;
}

async function installedGame({ connected = true } = {}) {
    const handlers = new Map<string, (frame: never) => void>();
    const sent: unknown[] = [];
    const lines: (HandedOver | null)[] = [];
    const pinned: (HandedOver | null)[] = [];
    const cards: (HandedOver | null)[] = [];
    const agentTiles: (AgentTile | null)[] = [];
    const toasts: Toast[] = [];
    const moreItems: Element[] = [];
    const callEndedHooks: (() => void)[] = [];
    const core: FeatureCore = {
        on: (type, handler) => void handlers.set(type, handler),
        send: (frame) => void sent.push(frame),
        isConnected: () => connected,
        showToast: (text, kind, action) => void toasts.push({ text, kind, action }),
        toastKind: TOAST_KIND,
        addMoreItems: (...items) => void moreItems.push(...items),
        receiveChatLine: (view) => void lines.push(handedOver(view)),
        pinLastChatLine: (view) => void pinned.push(handedOver(view)),
        showChatCard: (view) => void cards.push(handedOver(view)),
        showAgentTile: (tile) => void agentTiles.push(tile),
        showStage: () => undefined,
        drawAgentFigures: () => undefined,
        closeMore: () => undefined,
        onCallEnded: (hook) => void callEndedHooks.push(hook),
    };
    await install(core);
    const emit = (type: string, frame: object) => {
        const handler = handlers.get(type);
        if (handler === undefined) throw new Error(`no handler for ${type}`);
        (handler as (frame: object) => void)(frame);
    };
    const startButton = moreItems.find((item) => item.id === "gameStartBtn");
    if (!(startButton instanceof HTMLButtonElement)) throw new Error("no start button");
    const progress = startButton.querySelector("#gameProgress");
    const voiceButton = moreItems.find((item) => item.id === "gameVoiceBtn");
    if (!(voiceButton instanceof HTMLButtonElement)) throw new Error("no voice button");
    return {
        voiceButton,
        emit,
        sent,
        lines,
        pinned,
        cards,
        agentTiles,
        toasts,
        moreItems,
        startButton,
        progress,
        endCall: () => {
            callEndedHooks.forEach((hook) => {
                hook();
            });
        },
        seat: () => {
            emit(SIGNAL_TYPE.ROOM_CREATED, { participant_id: ME, is_host: false });
        },
    };
}

type Game = Awaited<ReturnType<typeof installedGame>>;

function startRound(game: Game, round: number, starter = ME) {
    game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.ANSWERING, round, rounds: 3, starter });
    game.emit(GAME_SIGNAL.GAME_LINE, { text: `Question ${String(round)}?`, round });
}

function lastCardProps(game: Game) {
    const card = game.cards.at(-1);
    if (card === null || card === undefined) throw new Error("no card is shown");
    return card.props as QuestionCardProps;
}

afterEach(() => {
    vi.unstubAllGlobals();
});

class FakeAudio {
    static readonly instances: FakeAudio[] = [];
    readonly listeners = new Map<string, () => void>();
    static playResult: () => Promise<void> = () => Promise.resolve();
    paused = false;

    constructor(readonly source: string) {
        FakeAudio.instances.push(this);
    }

    addEventListener(type: string, listener: () => void) {
        this.listeners.set(type, listener);
    }

    pause() {
        this.paused = true;
    }

    play() {
        return FakeAudio.playResult();
    }
}

/** A page whose voices come from `fetchVoice`, with the browser's audio and blob URLs replaced by fakes. */
function stubbedVoicePlayback(fetchVoice: (url: string) => Promise<Response>) {
    FakeAudio.instances.length = 0;
    const urls = { created: [] as Blob[], revoked: [] as string[] };
    // jsdom has no blob URLs, so the test supplies them and takes them away again.
    const createObjectURL = (blob: Blob) => {
        urls.created.push(blob);
        return `blob:voice-${String(urls.created.length)}`;
    };
    const revokeObjectURL = (url: string) => void urls.revoked.push(url);
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    onTestFinished(() => {
        Reflect.deleteProperty(URL, "createObjectURL");
        Reflect.deleteProperty(URL, "revokeObjectURL");
    });
    vi.stubGlobal("Audio", FakeAudio);
    const requested: string[] = [];
    vi.stubGlobal("fetch", (url: string) => {
        requested.push(url);
        return fetchVoice(url);
    });
    return { urls, requested };
}

/** A voice frame as the server sends it; the page fetches the path the frame names. */
const voiceFrame = (id: string) => ({ id, path: `/voices-of-the-server/${id}` });

const wavResponse = () => Promise.resolve(new Response(new Blob(["wav"]), { status: 200 }));

/** Lets every pending fetch and promise of the page run to its end. */
async function settled() {
    await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("installing the game", () => {
    it("adds Start quiz and the privacy note to More, and resolves", async () => {
        const game = await installedGame();

        expect(game.moreItems.map((item) => item.tagName)).toEqual(["BUTTON", "P", "BUTTON"]);
        expect(game.startButton.textContent).toContain(words["game.startButton"]);
        expect(game.moreItems[1]?.textContent).toContain("Datenschutz");
    });
});

describe("Start quiz in More", () => {
    it("asks the game master to start and then waits for its answer", async () => {
        const game = await installedGame();
        expect(game.startButton.disabled).toBe(false);

        game.startButton.click();

        expect(game.sent).toEqual([{ type: GAME_SIGNAL.GAME_START }]);
        expect(game.startButton.disabled).toBe(true);
    });

    it("GM-28: shows the game master thinking, then the running quiz and its progress", async () => {
        const game = await installedGame();
        game.seat();

        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.THINKING, round: 1, rounds: 3, starter: ME });
        expect(game.startButton.classList.contains("thinking")).toBe(true);
        expect(game.startButton.getAttribute("aria-busy")).toBe("true");
        expect(game.startButton.classList.contains("ending")).toBe(false);
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.ANSWERING, round: 1, rounds: 3, starter: ME });

        expect(game.startButton.classList.contains("thinking")).toBe(false);
        expect(game.startButton.getAttribute("aria-busy")).toBe("false");
        expect(game.startButton.classList.contains("running")).toBe(true);
        expect(game.startButton.classList.contains("ending")).toBe(true);
        expect(game.progress?.textContent).toBe(roundOfThree(1));
    });

    it("ends the quiz it started when it is pressed while one runs", async () => {
        const game = await installedGame();
        game.seat();
        startRound(game, 1);

        game.startButton.click();

        expect(game.sent).toEqual([{ type: GAME_SIGNAL.GAME_END }]);
    });

    it("cannot end a quiz someone else started and the person is no host", async () => {
        const game = await installedGame();
        game.emit(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participant_id: ME, is_host: false });

        startRound(game, 1, "someone");

        expect(game.startButton.classList.contains("running")).toBe(true);
        expect(game.startButton.classList.contains("ending")).toBe(false);
        expect(game.startButton.disabled).toBe(true);
    });

    it("lets the host end a quiz someone else started", async () => {
        const game = await installedGame();
        game.emit(SIGNAL_TYPE.ROOM_CREATED, { participant_id: ME, is_host: true });

        startRound(game, 1, "someone");

        expect(game.startButton.classList.contains("ending")).toBe(true);
        expect(game.startButton.disabled).toBe(false);
    });
});

describe("the game master's lines", () => {
    it("GM-07: hands over each line as a game master line with its text", async () => {
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_LINE, { text: "Welcome." });
        game.emit(GAME_SIGNAL.GAME_LINE, { text: "Good luck." });

        expect(game.lines).toEqual([
            { component: GameMasterLine, props: { text: "Welcome." } },
            { component: GameMasterLine, props: { text: "Good luck." } },
        ]);
    });

    it("pins the thinking bubble once while the game master thinks and takes it back after", async () => {
        const game = await installedGame();
        const thinking = { phase: GAME_PHASE.THINKING, round: 1, rounds: 3, starter: ME };
        expect(game.pinned).toEqual([]);

        game.emit(GAME_SIGNAL.GAME_PHASE, thinking);
        game.emit(GAME_SIGNAL.GAME_PHASE, thinking);
        expect(game.pinned).toEqual([{ component: GameMasterThinking, props: {} }]);
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.ANSWERING, round: 1, rounds: 3, starter: ME });

        expect(game.pinned).toEqual([{ component: GameMasterThinking, props: {} }, null]);
    });
});

describe("the question card", () => {
    it("is handed over once per round and changed in place after that", async () => {
        const game = await installedGame();
        game.seat();
        startRound(game, 1);
        expect(game.cards).toHaveLength(1);
        const props = lastCardProps(game);

        game.emit(GAME_SIGNAL.GAME_ANSWER_COUNT, { answered: 1, players: 2 });
        props.answer("Paris");

        expect(game.cards).toHaveLength(1);
        expect(game.cards[0]?.component).toBe(QuestionCard);
        expect(props).toMatchObject({
            tag: roundOfThree(1),
            question: "Question 1?",
            canReveal: true,
            status: words["game.answerSentCountStatus"]({ answered: "1", players: "2" }),
        });
        expect(game.sent).toEqual([{ type: GAME_SIGNAL.GAME_ANSWER, text: "Paris" }]);
    });

    it("is handed over again for the next round and taken back when no question is open", async () => {
        const game = await installedGame();
        game.seat();
        startRound(game, 1);
        const first = lastCardProps(game);

        startRound(game, 2);
        expect(lastCardProps(game)).not.toBe(first);
        expect(lastCardProps(game).tag).toBe(roundOfThree(2));
        game.emit(GAME_SIGNAL.GAME_LINE, { text: "It was Paris." });

        // The next round's phase frame comes before its question, so the old card steps aside first.
        expect(game.cards.map((card) => card?.component ?? null)).toEqual([QuestionCard, null, QuestionCard, null]);
    });

    it("asks to reveal through the card, and is taken back when the call ends", async () => {
        const game = await installedGame();
        game.seat();
        startRound(game, 1);

        lastCardProps(game).reveal();
        expect(game.sent).toEqual([{ type: GAME_SIGNAL.GAME_REVEAL }]);
        game.endCall();

        expect(game.cards.at(-1)).toBeNull();
        expect(game.progress?.textContent).toBe("");
    });
});

describe("refusals", () => {
    it.each([
        [GAME_REFUSAL.NO_ACCESS, words["game.noAccessToast"], "LOCKED"],
        [GAME_REFUSAL.SIGN_IN_AGAIN, words["game.signInAgainToast"](master), "WARNING"],
        [GAME_REFUSAL.BUSY, words["game.busyToast"](master), "WAIT"],
        [GAME_REFUSAL.BUDGET_SPENT, words["game.budgetSpentToast"], "LOCKED"],
        [GAME_REFUSAL.ALREADY_ANSWERING, words["game.alreadyAnsweringToast"](master), "WAIT"],
        [GAME_REFUSAL.QUIZ_RUNNING, words["game.quizRunningToast"], "WAIT"],
        [GAME_REFUSAL.NOT_STARTER, words["game.notStarterToast"], "LOCKED"],
        [GAME_REFUSAL.NOT_STARTER_OR_HOST, words["game.notStarterOrHostToast"], "LOCKED"],
        [GAME_REFUSAL.ANSWER_CLOSED, words["game.answerClosedToast"], "LOCKED"],
        [GAME_REFUSAL.ANSWER_TOO_LONG, words["game.answerTooLongToast"], "WARNING"],
        [GAME_REFUSAL.UNAVAILABLE, words["game.unavailableToast"](master), "WARNING"],
        ["from_the_future", words["game.unavailableToast"](master), "WARNING"],
    ] as const)("tells the person about %s", async (reason, text, kind) => {
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_REFUSED, { reason });

        expect(game.toasts).toMatchObject([{ text, kind: TOAST_KIND[kind] }]);
    });

    it("names the wait when the game master is busy", async () => {
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_REFUSED, { reason: GAME_REFUSAL.BUSY, retry_after_seconds: 12 });

        expect(game.toasts[0]?.text).toBe(words["game.busyRetryToast"](12, master));
    });

    it("GM-13: unlocks Start quiz after a refusal", async () => {
        const game = await installedGame();
        game.startButton.click();
        expect(game.startButton.disabled).toBe(true);

        game.emit(GAME_SIGNAL.GAME_REFUSED, { reason: GAME_REFUSAL.QUIZ_RUNNING });

        expect(game.startButton.disabled).toBe(false);
    });

    it("offers to sign in again, returning to the call", async () => {
        const assign = vi.fn<(url: string) => void>();
        vi.stubGlobal("location", { pathname: "/c/room", search: "?a=1", assign });
        const game = await installedGame();
        game.emit(GAME_SIGNAL.GAME_REFUSED, { reason: GAME_REFUSAL.SIGN_IN_AGAIN });
        const action = game.toasts[0]?.action;
        expect(assign).not.toHaveBeenCalled();

        action?.onAct();

        const target = new URL(assign.mock.calls[0]?.[0] ?? "", "http://hopin.test");
        expect(target.pathname).toBe(AUTH_START_PATH);
        expect(target.searchParams.get(RETURN_TO_QUERY_PARAMETER)).toBe("/c/room?a=1");
    });
});

describe("a quiz that ended because someone left", () => {
    it.each([
        [{ leaver: "Ada" }, words["game.endedByLeaverToast"]({ leaver: "Ada" })],
        [{}, words["game.endedToast"]],
    ])("tells the call: %j", async (frame, text) => {
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_ENDED, frame);

        expect(game.toasts).toMatchObject([{ text, kind: TOAST_KIND.FAILED, action: null }]);
    });
});

describe("a call that is not connected", () => {
    it("sends nothing from Start quiz", async () => {
        const game = await installedGame({ connected: false });

        game.startButton.click();

        expect(game.sent).toEqual([]);
        expect(game.startButton.disabled).toBe(false);
    });
});

describe("the game master's voice", () => {
    it("GM-32: fetches a voice from the game's route and starts playing it", async () => {
        const voices = stubbedVoicePlayback(wavResponse);
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        expect(voices.requested).toEqual([voiceFrame("voice-one").path]);
        expect(FakeAudio.instances.map((audio) => audio.source)).toEqual(["blob:voice-1"]);
    });

    it("GM-32: plays a voice once, so a replayed frame after a resume starts nothing", async () => {
        const voices = stubbedVoicePlayback(wavResponse);
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();
        game.seat();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        expect(voices.requested).toEqual([voiceFrame("voice-one").path]);
    });

    it("GM-33: stops a playing voice when the quiz ends", async () => {
        stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        game.emit(GAME_SIGNAL.GAME_ENDED, {});

        expect(FakeAudio.instances.map((audio) => audio.paused)).toEqual([true]);
    });

    it("GM-33: starts no voice that arrives from the network after the quiz ended", async () => {
        let deliver: (response: Response) => void = () => undefined;
        stubbedVoicePlayback(() => new Promise<Response>((resolve) => void (deliver = resolve)));
        const game = await installedGame();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));

        game.emit(GAME_SIGNAL.GAME_ENDED, {});
        deliver(new Response(new Blob(["wav"]), { status: 200 }));
        await settled();

        expect(FakeAudio.instances).toEqual([]);
    });

    it("GM-33: stops a playing voice when the call ends", async () => {
        stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        game.endCall();

        expect(FakeAudio.instances.map((audio) => audio.paused)).toEqual([true]);
    });

    it("stops the voice that plays when the next one starts", async () => {
        stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-two"));
        await settled();

        expect(FakeAudio.instances.map((audio) => audio.paused)).toEqual([true, false]);
    });

    it("GM-32: starts a voice whose fetch was still pending when the next voice arrived", async () => {
        const deliveries = new Map<string, (response: Response) => void>();
        stubbedVoicePlayback((url) => new Promise<Response>((resolve) => void deliveries.set(url, resolve)));
        const game = await installedGame();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-two"));

        deliveries.get(voiceFrame("voice-one").path)?.(new Response(new Blob(["wav"]), { status: 200 }));
        deliveries.get(voiceFrame("voice-two").path)?.(new Response(new Blob(["wav"]), { status: 200 }));
        await settled();

        expect(FakeAudio.instances.map((audio) => audio.source)).toEqual(["blob:voice-1", "blob:voice-2"]);
    });

    it("GM-32: starts a voice whose fetch finished after a newer voice's fetch", async () => {
        const deliveries = new Map<string, (response: Response) => void>();
        stubbedVoicePlayback((url) => new Promise<Response>((resolve) => void deliveries.set(url, resolve)));
        const game = await installedGame();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-two"));

        deliveries.get(voiceFrame("voice-two").path)?.(new Response(new Blob(["wav"]), { status: 200 }));
        deliveries.get(voiceFrame("voice-one").path)?.(new Response(new Blob(["wav"]), { status: 200 }));
        await settled();

        expect(FakeAudio.instances.map((audio) => audio.source)).toEqual(["blob:voice-1", "blob:voice-2"]);
    });

    it("GM-34: leaves the line as text when the voice is missing", async () => {
        stubbedVoicePlayback(() => Promise.resolve(new Response(null, { status: 404 })));
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        expect(FakeAudio.instances).toEqual([]);
    });

    it("GM-34: leaves the line as text when the voice cannot be fetched", async () => {
        stubbedVoicePlayback(() => Promise.reject(new Error("offline")));
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        expect(FakeAudio.instances).toEqual([]);
    });

    it("GM-34: lets go of the voice when the browser refuses to play it", async () => {
        const voices = stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        FakeAudio.playResult = () => Promise.reject(new Error("autoplay"));
        onTestFinished(() => {
            FakeAudio.playResult = () => Promise.resolve();
        });

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        expect(voices.urls.revoked).toEqual(["blob:voice-1"]);
    });

    it("lets go of the voice when it has played to its end", async () => {
        const voices = stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        FakeAudio.instances[0]?.listeners.get("ended")?.();

        expect(voices.urls.revoked).toEqual(["blob:voice-1"]);
    });
});

describe("muting the game master's voice", () => {
    const voicedQuiz = (game: Game) => {
        game.seat();
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.ANSWERING, round: 1, rounds: 3, starter: ME });
        game.emit(GAME_SIGNAL.GAME_LINE, { text: "Question 1?", round: 1, voiced: true });
        game.voiceButton.click();
    };

    it("GM-37: fetches no voice while muted, and the tile never speaks", async () => {
        const voices = stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        voicedQuiz(game);

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        expect([voices.requested, FakeAudio.instances, lastTile(game)]).toEqual([
            [],
            [],
            tileOf(AGENT_ACTIVITY.WAITING),
        ]);
        expect(game.voiceButton).toMatchObject({
            hidden: false,
            textContent: words["game.voiceLabel"](master) + words["game.voiceOff"],
        });
        expect(game.voiceButton.getAttribute("aria-pressed")).toBe("false");
    });

    it("GM-37: stops a playing voice at once on mute", async () => {
        stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        voicedQuiz(game);
        game.voiceButton.click();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        game.voiceButton.click();

        expect([FakeAudio.instances[0]?.paused, lastTile(game)]).toEqual([true, tileOf(AGENT_ACTIVITY.WAITING)]);
    });

    it("GM-37: plays from the next line on after unmuting, never a line already gone", async () => {
        const voices = stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        voicedQuiz(game);
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        game.voiceButton.click();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-two"));
        await settled();

        expect(voices.requested).toEqual([voiceFrame("voice-two").path]);
    });
});

const tileOf = (activity: AgentTile["activity"]): AgentTile => ({ name: master.name, activity });
const thinkingPhase = { phase: GAME_PHASE.THINKING, round: 1, rounds: 3, starter: ME };

function lastTile(game: Game) {
    return game.agentTiles.at(-1) ?? null;
}

describe("the game master's tile", () => {
    it("GM-35: shows from the start's thinking, waits at the question and goes with the quiz", async () => {
        const game = await installedGame();
        game.seat();

        game.emit(GAME_SIGNAL.GAME_PHASE, thinkingPhase);
        const whileThinking = lastTile(game);
        startRound(game, 1);
        const whileAnswering = lastTile(game);
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.IDLE });

        expect([whileThinking, whileAnswering, lastTile(game)]).toEqual([
            tileOf(AGENT_ACTIVITY.THINKING),
            tileOf(AGENT_ACTIVITY.WAITING),
            null,
        ]);
    });

    it("GM-35: hands the stage a tile only when what it shows changes", async () => {
        const game = await installedGame();
        game.seat();

        startRound(game, 1);
        game.emit(GAME_SIGNAL.GAME_ANSWER_COUNT, { answered: 1, players: 2 });
        startRound(game, 2);

        expect(game.agentTiles).toEqual([tileOf(AGENT_ACTIVITY.WAITING)]);
    });

    it("GM-35: shows a late joiner the tile of the phase it is greeted with", async () => {
        const game = await installedGame();

        game.seat();
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.ANSWERING, round: 2, rounds: 3, starter: "someone" });

        expect(lastTile(game)).toEqual(tileOf(AGENT_ACTIVITY.WAITING));
    });

    it("GM-35: speaks while a voice plays, then waits again", async () => {
        stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        game.seat();
        startRound(game, 1);

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();
        const whilePlaying = lastTile(game);
        FakeAudio.instances[0]?.listeners.get("ended")?.();

        expect([whilePlaying, lastTile(game)]).toEqual([
            tileOf(AGENT_ACTIVITY.SPEAKING),
            tileOf(AGENT_ACTIVITY.WAITING),
        ]);
    });

    it("GM-36: keeps the tile through the gap after the final line's idle until its voice has played", async () => {
        stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        game.seat();
        startRound(game, 1);
        game.emit(GAME_SIGNAL.GAME_LINE, { text: "Final score", voiced: true });
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.IDLE });
        const inTheGap = lastTile(game);

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("score"));
        const whileFetching = lastTile(game);
        await settled();
        const whilePlaying = lastTile(game);
        FakeAudio.instances[0]?.listeners.get("ended")?.();

        expect([inTheGap, whileFetching, whilePlaying, lastTile(game)]).toEqual([
            tileOf(AGENT_ACTIVITY.WAITING),
            tileOf(AGENT_ACTIVITY.WAITING),
            tileOf(AGENT_ACTIVITY.SPEAKING),
            null,
        ]);
        expect(game.agentTiles).toEqual([tileOf(AGENT_ACTIVITY.WAITING), tileOf(AGENT_ACTIVITY.SPEAKING), null]);
    });

    it("GM-36: ends the tile at the empty voice frame of a final line whose voice failed", async () => {
        const game = await installedGame();
        game.seat();
        startRound(game, 1);
        game.emit(GAME_SIGNAL.GAME_LINE, { text: "Final score", voiced: true });
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.IDLE });
        const inTheGap = lastTile(game);

        game.emit(GAME_SIGNAL.GAME_VOICE, {});

        expect([inTheGap, lastTile(game)]).toEqual([tileOf(AGENT_ACTIVITY.WAITING), null]);
    });

    it("GM-36: ends the tile at idle when the final line is not voiced", async () => {
        const game = await installedGame();
        game.seat();
        startRound(game, 1);
        game.emit(GAME_SIGNAL.GAME_LINE, { text: "Final score" });

        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.IDLE });

        expect(lastTile(game)).toBeNull();
    });

    it("GM-36: ends the tile when the browser refuses the final voice's playback", async () => {
        stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        game.seat();
        startRound(game, 1);
        game.emit(GAME_SIGNAL.GAME_LINE, { text: "Final score", voiced: true });
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.IDLE });
        FakeAudio.playResult = () => Promise.reject(new Error("autoplay"));
        onTestFinished(() => {
            FakeAudio.playResult = () => Promise.resolve();
        });

        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("score"));
        await settled();

        expect(lastTile(game)).toBeNull();
    });

    it("GM-35: goes when the final voice cannot be fetched", async () => {
        stubbedVoicePlayback(() => Promise.reject(new Error("offline")));
        const game = await installedGame();
        game.seat();
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("score"));

        await settled();

        expect(lastTile(game)).toBeNull();
    });

    it("GM-35: stops speaking when the quiz ends and goes with the idle that follows", async () => {
        stubbedVoicePlayback(wavResponse);
        const game = await installedGame();
        game.seat();
        startRound(game, 1);
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));
        await settled();

        game.emit(GAME_SIGNAL.GAME_ENDED, {});
        const afterEnded = lastTile(game);
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.IDLE });

        expect([afterEnded, lastTile(game)]).toEqual([tileOf(AGENT_ACTIVITY.WAITING), null]);
    });

    it("GM-35: shows no tile again when a voice starts late, after the call ended", async () => {
        let deliver: (response: Response) => void = () => undefined;
        stubbedVoicePlayback(() => new Promise<Response>((resolve) => void (deliver = resolve)));
        const game = await installedGame();
        game.seat();
        startRound(game, 1);
        game.emit(GAME_SIGNAL.GAME_VOICE, voiceFrame("voice-one"));

        game.endCall();
        deliver(new Response(new Blob(["wav"]), { status: 200 }));
        await settled();

        expect(lastTile(game)).toBeNull();
    });

    it("GM-35: carries no tile into the next call after a call ended with the quiz running", async () => {
        const game = await installedGame();
        game.seat();
        startRound(game, 1);

        game.endCall();
        const afterTheCallEnded = lastTile(game);
        game.seat();

        expect([afterTheCallEnded, lastTile(game)]).toEqual([null, null]);
    });

    it("GM-35: a fresh seat after a refused resume starts without the tile", async () => {
        const game = await installedGame();
        game.seat();
        startRound(game, 1);

        game.seat();

        expect(lastTile(game)).toBeNull();
    });
});
