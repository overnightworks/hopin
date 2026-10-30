// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushSync } from "svelte";
import type { ServerFrame } from "#lib/call/signaling.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";

const signaling = vi.hoisted(() => ({
    isConnected: vi.fn(),
    on: vi.fn<(type: string, handler: (data: unknown) => void) => void>(),
    send: vi.fn(),
}));
const sounds = vi.hoisted(() => ({ playMessageSound: vi.fn() }));
// jsdom renders no Svelte tree: a case sets what its flushSync puts in the list.
const rendering = vi.hoisted(() => ({ render: vi.fn() }));
vi.mock("#lib/call/signaling.ts", () => signaling);
vi.mock("#lib/call/sounds.ts", () => sounds);
vi.mock("svelte", () => ({ flushSync: () => void rendering.render() }));

// The module holds the one chat, so each case loads a fresh copy.
async function freshChat() {
    vi.resetModules();
    const chatModule = await import("#lib/ui/chat.svelte.ts");
    const { state } = await import("#lib/call/state.ts");
    return { ...chatModule, state };
}

type Chat = Awaited<ReturnType<typeof freshChat>>;

function registeredChatHandler(): (data: ServerFrame) => void {
    const [type, handler] = signaling.on.mock.calls[0] ?? [];
    expect(type).toBe(SIGNAL_TYPE.CHAT);
    return handler as (data: ServerFrame) => void;
}

function receive(data: Record<string, unknown>) {
    registeredChatHandler()({ type: SIGNAL_TYPE.CHAT, ...data });
}

// jsdom computes no layout: the list is as tall as it has children.
function registerList(chat: Chat, list: HTMLElement) {
    Object.defineProperty(list, "scrollHeight", { get: () => list.childElementCount });
    return chat.chatList(list);
}

beforeEach(() => {
    signaling.isConnected.mockReturnValue(true);
});

afterEach(() => {
    vi.clearAllMocks();
    rendering.render.mockReset();
    document.body.innerHTML = "";
});

describe("opening and closing", () => {
    it("starts closed with nothing unread", async () => {
        const { chat } = await freshChat();

        expect(chat).toMatchObject({ isOpen: false, unread: 0, draft: "", lines: [], pinned: null, card: null });
    });

    it("opens and closes without clearing the lines", async () => {
        const { chat, toggleChat, closeChat, receiveLine } = await freshChat();
        receiveLine(document.createElement("div"));

        toggleChat();
        expect(chat.isOpen).toBe(true);
        closeChat();

        expect(chat.isOpen).toBe(false);
        expect(chat.lines).toHaveLength(1);
    });

    it("toggles shut again", async () => {
        const { chat, toggleChat } = await freshChat();

        toggleChat();
        toggleChat();

        expect(chat.isOpen).toBe(false);
    });

    it("zeroes the unread count and focuses the input when it opens", async () => {
        const chat = await freshChat();
        chat.receiveLine(document.createElement("div"));
        const input = document.createElement("input");
        document.body.append(input);
        const stop = $effect.root(() => chat.chatInput(input));

        chat.toggleChat();

        expect(chat.chat.unread).toBe(0);
        expect(document.activeElement).toBe(input);
        stop();
    });

    it("opens without an input registered", async () => {
        const { chat, toggleChat } = await freshChat();

        toggleChat();

        expect(chat.isOpen).toBe(true);
    });

    it("forgets the input when its attachment is torn down", async () => {
        const chat = await freshChat();
        const input = document.createElement("input");
        document.body.append(input);
        const stop = $effect.root(() => chat.chatInput(input));
        stop();

        chat.toggleChat();

        expect(document.activeElement).not.toBe(input);
    });
});

describe("unread lines", () => {
    it("counts a line and plays the sound while the panel is closed", async () => {
        const { chat, receiveLine } = await freshChat();

        receiveLine(document.createElement("div"));
        receiveLine(document.createElement("div"));

        expect(chat.unread).toBe(2);
        expect(sounds.playMessageSound).toHaveBeenCalledTimes(2);
    });

    it("neither counts nor sounds while the panel is open", async () => {
        const { chat, toggleChat, receiveLine } = await freshChat();
        toggleChat();

        receiveLine(document.createElement("div"));

        expect(chat.unread).toBe(0);
        expect(sounds.playMessageSound).not.toHaveBeenCalled();
    });
});

describe("sending", () => {
    it("sends the trimmed draft, shows it as an own line and clears the draft", async () => {
        const { chat, sendChatMessage } = await freshChat();
        chat.draft = "  hello  ";

        sendChatMessage();

        expect(signaling.send).toHaveBeenCalledWith({ type: SIGNAL_TYPE.CHAT, message: "hello" });
        expect(chat.lines).toEqual([{ kind: "self", text: "hello" }]);
        expect(chat.draft).toBe("");
        expect(chat.unread).toBe(0);
    });

    it.each([
        ["empty", ""],
        ["blank", "   "],
    ])("ignores an %s draft", async (_name, draft) => {
        const { chat, sendChatMessage } = await freshChat();
        chat.draft = draft;

        sendChatMessage();

        expect(signaling.send).not.toHaveBeenCalled();
        expect(chat.lines).toEqual([]);
    });

    it("keeps the draft while the connection is lost", async () => {
        signaling.isConnected.mockReturnValue(false);
        const { chat, sendChatMessage } = await freshChat();
        chat.draft = "hello";

        sendChatMessage();

        expect(signaling.send).not.toHaveBeenCalled();
        expect(chat.draft).toBe("hello");
        expect(chat.lines).toEqual([]);
    });

    it("sends on Enter and on no other key", async () => {
        const { chat, sendOnEnter } = await freshChat();
        chat.draft = "hello";

        sendOnEnter(new KeyboardEvent("keydown", { key: "a" }));
        expect(signaling.send).not.toHaveBeenCalled();
        sendOnEnter(new KeyboardEvent("keydown", { key: "Enter" }));

        expect(signaling.send).toHaveBeenCalledOnce();
    });
});

describe("receiving", () => {
    it("shows another person's line with the sender's name and counts it", async () => {
        const chat = await freshChat();
        chat.state.namesByParticipant.set("p1", "Ada");
        chat.registerChatHandlers();

        receive({ message: "hi", from: "p1" });

        expect(chat.chat.lines).toEqual([{ kind: "other", text: "hi", sender: "Ada" }]);
        expect(chat.chat.unread).toBe(1);
        expect(sounds.playMessageSound).toHaveBeenCalledOnce();
    });

    it("shows an unknown sender with an empty name", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();

        receive({ message: "hi", from: "nobody" });

        expect(chat.chat.lines).toEqual([{ kind: "other", text: "hi", sender: "" }]);
    });

    it.each([
        ["no message", { from: "p1" }],
        ["a message that is no text", { message: 5, from: "p1" }],
        ["no sender", { message: "hi" }],
        ["a sender that is no text", { message: "hi", from: 5 }],
    ])("ignores a frame with %s", async (_name, frame) => {
        const chat = await freshChat();
        chat.registerChatHandlers();

        receive(frame);

        expect(chat.chat.lines).toEqual([]);
        expect(chat.chat.unread).toBe(0);
    });
});

describe("pinned element and card", () => {
    it("replaces and clears the pinned element", async () => {
        const { chat, pinLast } = await freshChat();
        const first = document.createElement("div");
        const second = document.createElement("div");

        pinLast(first);
        pinLast(second);
        expect(chat.pinned).toBe(second);
        pinLast(null);

        expect(chat.pinned).toBeNull();
    });

    it("replaces and clears the card", async () => {
        const { chat, showCard } = await freshChat();
        const first = document.createElement("div");
        const second = document.createElement("div");

        showCard(first);
        showCard(second);
        expect(chat.card).toBe(second);
        showCard(null);

        expect(chat.card).toBeNull();
    });
});

describe("scrolling to the newest line", () => {
    it("scrolls after the new line is rendered into the list", async () => {
        const chat = await freshChat();
        const list = document.createElement("div");
        const stop = $effect.root(() => registerList(chat, list));
        rendering.render.mockImplementation(() => {
            while (list.childElementCount < chat.chat.lines.length) list.append(document.createElement("div"));
        });

        chat.receiveLine(document.createElement("p"));

        expect(list.childElementCount).toBe(1);
        expect(list.scrollTop).toBe(list.scrollHeight);
        stop();
    });

    it("scrolls after pinning an element and not after clearing it", async () => {
        const chat = await freshChat();
        const list = document.createElement("div");
        const stop = $effect.root(() => registerList(chat, list));
        list.append(document.createElement("div"));

        chat.pinLast(null);
        expect(list.scrollTop).toBe(0);
        chat.pinLast(document.createElement("div"));

        expect(list.scrollTop).toBe(list.scrollHeight);
        stop();
    });

    it("scrolls after an own line", async () => {
        const chat = await freshChat();
        const list = document.createElement("div");
        const stop = $effect.root(() => registerList(chat, list));
        list.append(document.createElement("div"));
        chat.chat.draft = "hello";

        chat.sendChatMessage();

        expect(list.scrollTop).toBe(list.scrollHeight);
        stop();
    });

    it("takes a line without a list registered, and after its teardown", async () => {
        const chat = await freshChat();
        const list = document.createElement("div");
        $effect.root(() => registerList(chat, list))();
        list.append(document.createElement("div"));

        chat.receiveLine(document.createElement("div"));

        expect(list.scrollTop).toBe(0);
    });
});

describe("adopt", () => {
    it("appends the element itself into its wrapper and removes it on teardown", async () => {
        const { adopt } = await freshChat();
        const wrapper = document.createElement("div");
        const element = document.createElement("p");

        const stop = $effect.root(() => adopt(element)(wrapper));
        flushSync();
        expect(wrapper.firstChild).toBe(element);
        stop();

        expect(element.isConnected).toBe(false);
    });
});
