import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ERROR_REASON, SESSION_ENDED_CLOSE_CODE, SIGNAL_TYPE, WEBSOCKET_PATH } from "#lib/wire.ts";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));

class FakeSocket {
    static instances: FakeSocket[] = [];
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: ((event: { code: number }) => void) | null = null;
    onerror: (() => void) | null = null;
    readonly sent: Record<string, unknown>[] = [];
    readonly close = vi.fn();

    constructor(readonly url: string) {
        FakeSocket.instances.push(this);
    }

    send(data: string) {
        this.sent.push(JSON.parse(data) as Record<string, unknown>);
    }

    open() {
        this.onopen?.();
    }

    receive(frame: Record<string, unknown>) {
        this.onmessage?.({ data: JSON.stringify(frame) });
    }

    closeWith(code: number) {
        this.onclose?.({ code });
    }
}

function stubPage(pathname = "/", protocol = "https:") {
    vi.stubGlobal("location", { protocol, host: "hopin.test", pathname });
}

// The module holds its state, so each case loads a fresh copy.
async function freshSignaling() {
    vi.resetModules();
    FakeSocket.instances = [];
    const signaling = await import("#lib/call/signaling.ts");
    const { state } = await import("#lib/call/state.ts");
    const { statusLine } = await import("#lib/ui/status.svelte.ts");
    return { ...signaling, state, statusLine };
}

type Signaling = Awaited<ReturnType<typeof freshSignaling>>;

function latestSocket() {
    return FakeSocket.instances[FakeSocket.instances.length - 1] as FakeSocket;
}

function seatedSocket(signaling: Signaling, resumeToken = "token-1") {
    signaling.connect();
    const socket = latestSocket();
    socket.open();
    socket.receive({ type: SIGNAL_TYPE.ROOM_CREATED, participant_id: "me", resume_token: resumeToken, seq: 1 });
    return socket;
}

beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
    stubPage();
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

describe("connecting", () => {
    it("refuses to send before connect", async () => {
        const { send } = await freshSignaling();

        expect(() => {
            send({ type: SIGNAL_TYPE.CHAT });
        }).toThrow("No signaling session");
    });

    it.each([
        ["https:", "wss:"],
        ["http:", "ws:"],
    ])("opens the socket for a %s page on %s", async (protocol, scheme) => {
        stubPage("/", protocol);
        const { connect, isConnected } = await freshSignaling();

        expect(isConnected()).toBe(false);
        connect();

        expect(isConnected()).toBe(true);
        expect(latestSocket().url).toBe(`${scheme}//hopin.test${WEBSOCKET_PATH}`);
    });

    it("joins the room named by the address", async () => {
        stubPage("/c/abc");
        const { connect, statusLine } = await freshSignaling();

        connect();
        latestSocket().open();

        expect(latestSocket().sent).toEqual([{ type: SIGNAL_TYPE.JOIN, room_id: "abc" }]);
        expect(statusLine).toEqual({ text: "Connected - joining...", type: "connecting" });
    });

    it("joins the room of the call state when the address names none", async () => {
        const { connect, state } = await freshSignaling();
        state.roomId = "from-state";

        connect();
        latestSocket().open();

        expect(latestSocket().sent).toEqual([{ type: SIGNAL_TYPE.JOIN, room_id: "from-state" }]);
    });

    it("joins without a room when neither names one", async () => {
        const { connect } = await freshSignaling();

        connect();
        latestSocket().open();

        expect(latestSocket().sent).toEqual([{ type: SIGNAL_TYPE.JOIN }]);
    });

    it("shows a socket error in the status line", async () => {
        const { connect, statusLine } = await freshSignaling();

        connect();
        latestSocket().onerror?.();

        expect(statusLine).toEqual({ text: "Connection error", type: "error" });
    });
});

describe("sending", () => {
    it("holds frames until the join was written, then sends them numbered in order", async () => {
        const { connect, send } = await freshSignaling();
        connect();

        send({ type: SIGNAL_TYPE.CHAT, text: "early" });
        expect(latestSocket().sent).toEqual([]);
        latestSocket().open();

        expect(latestSocket().sent).toEqual([
            { type: SIGNAL_TYPE.JOIN },
            { type: SIGNAL_TYPE.CHAT, text: "early", seq: 1 },
        ]);
    });

    it("writes a frame at once once the socket is live", async () => {
        const { connect, send } = await freshSignaling();
        connect();
        latestSocket().open();

        send({ type: SIGNAL_TYPE.CHAT, text: "now" });

        expect(latestSocket().sent[1]).toEqual({ type: SIGNAL_TYPE.CHAT, text: "now", seq: 1 });
    });
});

describe("receiving", () => {
    it("hands a frame to every handler of its type and to none of another", async () => {
        const { connect, on } = await freshSignaling();
        const first = vi.fn();
        const second = vi.fn();
        const other = vi.fn();
        on(SIGNAL_TYPE.CHAT, first);
        on(SIGNAL_TYPE.CHAT, second);
        on(SIGNAL_TYPE.MEDIA_STATE, other);
        connect();

        latestSocket().receive({ type: SIGNAL_TYPE.CHAT, text: "hi" });

        expect(first).toHaveBeenCalledWith({ type: SIGNAL_TYPE.CHAT, text: "hi" });
        expect(second).toHaveBeenCalledOnce();
        expect(other).not.toHaveBeenCalled();
    });

    it("hands a frame of a type the core does not know to the feature handler registered for it", async () => {
        const { connect, onFeatureFrame } = await freshSignaling();
        const handler = vi.fn();
        onFeatureFrame("game_move", handler);
        connect();

        latestSocket().receive({ type: "game_move", cell: 4 });

        expect(handler).toHaveBeenCalledWith({ type: "game_move", cell: 4 });
    });

    it("drops a frame no handler takes", async () => {
        const { connect } = await freshSignaling();
        connect();

        expect(() => {
            latestSocket().receive({ type: SIGNAL_TYPE.HOST_AWAY });
        }).not.toThrow();
    });

    it("hands over frames without a number each time and drops a repeated number", async () => {
        const { connect, on } = await freshSignaling();
        const handler = vi.fn();
        on(SIGNAL_TYPE.CHAT, handler);
        connect();

        latestSocket().receive({ type: SIGNAL_TYPE.CHAT });
        latestSocket().receive({ type: SIGNAL_TYPE.CHAT });
        latestSocket().receive({ type: SIGNAL_TYPE.CHAT, seq: 4 });
        latestSocket().receive({ type: SIGNAL_TYPE.CHAT, seq: 4 });
        latestSocket().receive({ type: SIGNAL_TYPE.CHAT, seq: 3 });
        latestSocket().receive({ type: SIGNAL_TYPE.CHAT, seq: 5 });

        expect(handler).toHaveBeenCalledTimes(4);
    });
});

describe("losing the connection", () => {
    it("ends a session that never held a seat", async () => {
        const { connect, isConnected, onSessionLost, statusLine } = await freshSignaling();
        const lost = vi.fn();
        onSessionLost(lost);
        connect();

        latestSocket().closeWith(1006);

        expect(lost).toHaveBeenCalledOnce();
        expect(isConnected()).toBe(false);
        expect(statusLine).toEqual({ text: "Disconnected", type: "disconnected" });
    });

    it.each([1006, SESSION_ENDED_CLOSE_CODE])(
        "ends the session on close code %s with no callback set",
        async (code) => {
            const { connect, isConnected } = await freshSignaling();
            connect();

            latestSocket().closeWith(code);

            expect(isConnected()).toBe(false);
        },
    );

    it("leaves a deliberate leave alone", async () => {
        const { connect, isConnected, onSessionLost, state } = await freshSignaling();
        const lost = vi.fn();
        onSessionLost(lost);
        connect();
        state.intentionalLeave = true;

        latestSocket().closeWith(1000);

        expect(lost).not.toHaveBeenCalled();
        expect(isConnected()).toBe(true);
    });

    it("reports a session the server ended", async () => {
        const signaling = await freshSignaling();
        const { isConnected, onSessionEnded, onSessionLost } = signaling;
        const lost = vi.fn();
        const ended = vi.fn();
        onSessionLost(lost);
        onSessionEnded(ended);
        const socket = seatedSocket(signaling);

        socket.closeWith(SESSION_ENDED_CLOSE_CODE);

        expect(ended).toHaveBeenCalledOnce();
        expect(lost).not.toHaveBeenCalled();
        expect(isConnected()).toBe(false);
    });

    it("gives up with a lost connection once the delays are used up", async () => {
        const signaling = await freshSignaling();
        const lost = vi.fn();
        signaling.onSessionLost(lost);
        const socket = seatedSocket(signaling);
        signaling.state.reconnectAttempt = signaling.RECONNECT_DELAYS_MS.length;

        socket.closeWith(1006);

        expect(lost).toHaveBeenCalledOnce();
        expect(signaling.statusLine).toEqual({ text: "Connection lost", type: "error" });
    });

    it("ignores the events of a socket that is no longer the current one", async () => {
        const signaling = await freshSignaling();
        signaling.connect();
        const stale = latestSocket();
        signaling.connect();

        stale.closeWith(1006);

        expect(signaling.isConnected()).toBe(true);
    });
});

describe("resuming", () => {
    it("reconnects after the first delay and asks to resume with what it saw and sent", async () => {
        const signaling = await freshSignaling();
        const socket = seatedSocket(signaling);
        signaling.send({ type: SIGNAL_TYPE.CHAT, text: "kept" });

        socket.closeWith(1006);
        expect(signaling.statusLine).toEqual({ text: "Reconnecting in 1s...", type: "connecting" });
        expect(FakeSocket.instances).toHaveLength(1);
        vi.advanceTimersByTime(signaling.RECONNECT_DELAYS_MS[0] as number);
        latestSocket().open();

        expect(FakeSocket.instances).toHaveLength(2);
        expect(latestSocket().sent).toEqual([
            { type: SIGNAL_TYPE.RESUME, participant_id: "me", token: "token-1", last_seq: 1, sent_floor: 0 },
        ]);
    });

    it("takes the seat back, drops what the server saw and resends the rest", async () => {
        const signaling = await freshSignaling();
        const socket = seatedSocket(signaling);
        signaling.send({ type: SIGNAL_TYPE.CHAT, text: "seen" });
        signaling.send({ type: SIGNAL_TYPE.CHAT, text: "missed" });
        socket.closeWith(1006);
        vi.advanceTimersByTime(1000);
        latestSocket().open();

        latestSocket().receive({ type: SIGNAL_TYPE.RESUMED, resume_token: "token-2", last_seq: 1 });

        expect(latestSocket().sent.slice(1)).toEqual([{ type: SIGNAL_TYPE.CHAT, text: "missed", seq: 2 }]);
        expect(signaling.state.reconnectAttempt).toBe(0);
        expect(signaling.statusLine).toEqual({ text: "In call", type: "connected" });
        latestSocket().closeWith(1006);
        vi.advanceTimersByTime(1000);
        latestSocket().open();
        expect(latestSocket().sent[0]).toMatchObject({ type: SIGNAL_TYPE.RESUME, token: "token-2" });
    });

    it("resends a feature frame after a resume", async () => {
        const signaling = await freshSignaling();
        const socket = seatedSocket(signaling);
        signaling.sendFeatureFrame({ type: "game_move", cell: 4 });
        expect(socket.sent.at(-1)).toEqual({ type: "game_move", cell: 4, seq: 1 });
        socket.closeWith(1006);
        vi.advanceTimersByTime(1000);
        latestSocket().open();

        latestSocket().receive({ type: SIGNAL_TYPE.RESUMED, resume_token: "token-2", last_seq: 0 });

        expect(latestSocket().sent.slice(1)).toEqual([{ type: "game_move", cell: 4, seq: 1 }]);
    });

    it("joins afresh when the server refuses the resume", async () => {
        const signaling = await freshSignaling();
        const lost = vi.fn();
        signaling.onSessionLost(lost);
        const socket = seatedSocket(signaling);
        signaling.send({ type: SIGNAL_TYPE.CHAT, text: "old" });
        socket.closeWith(1006);
        vi.advanceTimersByTime(1000);
        latestSocket().open();

        latestSocket().receive({ type: SIGNAL_TYPE.ERROR, reason: ERROR_REASON.RESUME_REFUSED });

        expect(lost).toHaveBeenCalledOnce();
        expect(latestSocket().sent.slice(1)).toEqual([{ type: SIGNAL_TYPE.JOIN, seq: 1 }]);
    });

    it("hands any other error on to its handlers", async () => {
        const { connect, on } = await freshSignaling();
        const handler = vi.fn();
        on(SIGNAL_TYPE.ERROR, handler);
        connect();

        latestSocket().receive({ type: SIGNAL_TYPE.ERROR, reason: ERROR_REASON.FULL });

        expect(handler).toHaveBeenCalledOnce();
    });
});

describe("disconnecting", () => {
    it("closes the socket normally and forgets the session", async () => {
        const { connect, disconnect, isConnected } = await freshSignaling();
        connect();

        disconnect();

        expect(latestSocket().close).toHaveBeenCalledWith(1000);
        expect(isConnected()).toBe(false);
    });

    it("cancels a reconnect that waits", async () => {
        const signaling = await freshSignaling();
        seatedSocket(signaling).closeWith(1006);

        signaling.disconnect();
        vi.advanceTimersByTime(10_000);

        expect(FakeSocket.instances).toHaveLength(1);
    });

    it("does nothing without a session", async () => {
        const { disconnect, isConnected } = await freshSignaling();

        disconnect();

        expect(isConnected()).toBe(false);
    });
});
