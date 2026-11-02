// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FeatureCore } from "#lib/call/feature-port.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import { TOAST_KIND, type ToastAction, type ToastKind } from "#lib/ui/toast.svelte.ts";
import { AUTH_START_PATH, RETURN_TO_QUERY_PARAMETER, SIGNAL_TYPE } from "#lib/wire.ts";
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
    return {
        emit,
        sent,
        lines,
        pinned,
        cards,
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

describe("installing the game", () => {
    it("adds Start quiz and the privacy note to More, and resolves", async () => {
        const game = await installedGame();

        expect(game.moreItems.map((item) => item.tagName)).toEqual(["BUTTON", "P"]);
        expect(game.startButton.textContent).toContain("Start quiz");
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

    it("shows the game master thinking, then the running quiz and its progress", async () => {
        const game = await installedGame();
        game.seat();

        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.THINKING, round: 1, rounds: 3, starter: ME });
        expect(game.startButton.classList.contains("thinking")).toBe(true);
        expect(game.startButton.getAttribute("aria-busy")).toBe("true");
        game.emit(GAME_SIGNAL.GAME_PHASE, { phase: GAME_PHASE.ANSWERING, round: 1, rounds: 3, starter: ME });

        expect(game.startButton.classList.contains("thinking")).toBe(false);
        expect(game.startButton.getAttribute("aria-busy")).toBe("false");
        expect(game.startButton.classList.contains("running")).toBe(true);
        expect(game.startButton.classList.contains("ending")).toBe(true);
        expect(game.progress?.textContent).toBe("Question 1 of 3");
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
    it("hands over each line as a game master line with its text", async () => {
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
            tag: "Question 1 of 3",
            question: "Question 1?",
            canReveal: true,
            status: "Answer sent · 1 of 2 answered",
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
        expect(lastCardProps(game).tag).toBe("Question 2 of 3");
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
        [GAME_REFUSAL.NO_ACCESS, "Your account has no AI access.", "LOCKED"],
        [GAME_REFUSAL.SIGN_IN_AGAIN, "Sign in again to use the game master.", "WARNING"],
        [GAME_REFUSAL.BUSY, "The game master is busy.", "WAIT"],
        [GAME_REFUSAL.ALREADY_ANSWERING, "The game master is already answering.", "WAIT"],
        [GAME_REFUSAL.QUIZ_RUNNING, "A quiz is already running.", "WAIT"],
        [GAME_REFUSAL.NOT_STARTER, "Only the person who started the quiz can reveal.", "LOCKED"],
        [GAME_REFUSAL.NOT_STARTER_OR_HOST, "Only the person who started the quiz or the host can end it.", "LOCKED"],
        [GAME_REFUSAL.ANSWER_CLOSED, "This question is already closed.", "LOCKED"],
        [GAME_REFUSAL.ANSWER_TOO_LONG, "Your answer is too long. Shorten it and send again.", "WARNING"],
        [GAME_REFUSAL.UNAVAILABLE, "The game master is unavailable right now.", "WARNING"],
    ] as const)("tells the person about %s", async (reason, text, kind) => {
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_REFUSED, { reason });

        expect(game.toasts).toMatchObject([{ text, kind: TOAST_KIND[kind] }]);
    });

    it("falls back to unavailable for a reason it does not know", async () => {
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_REFUSED, { reason: "from_the_future" });

        expect(game.toasts).toMatchObject([{ text: "The game master is unavailable right now." }]);
    });

    it("names the wait when the game master is busy", async () => {
        const game = await installedGame();

        game.emit(GAME_SIGNAL.GAME_REFUSED, { reason: GAME_REFUSAL.BUSY, retry_after_seconds: 12 });

        expect(game.toasts[0]?.text).toBe("The game master is busy. Try again in 12 s.");
    });

    it("unlocks Start quiz after a refusal", async () => {
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
        [{ leaver: "Ada" }, "Quiz ended: Ada left."],
        [{}, "Quiz ended."],
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
