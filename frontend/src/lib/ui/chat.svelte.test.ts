// @vitest-environment jsdom
import type { ComponentInternals } from "svelte";
import type { ChatLine, FeatureView } from "#lib/ui/chat.svelte.ts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LINE_MAX_CHARACTERS } from "#lib/wire.ts";
import { words } from "#lib/words.ts";

const sounds = vi.hoisted(() => ({ playMessageSound: vi.fn() }));
// jsdom renders no Svelte tree: a case sets what its flushSync puts in the list.
const rendering = vi.hoisted(() => ({ render: vi.fn() }));
vi.mock("#lib/call/sounds.ts", () => sounds);
vi.mock("svelte", () => ({ flushSync: () => void rendering.render() }));

// The module holds the one chat, so each case loads a fresh copy.
async function freshChat() {
    vi.resetModules();
    const chatModule = await import("#lib/ui/chat.svelte.ts");
    const { state } = await import("#lib/call/state.ts");
    const { lines } = await import("#lib/call/lines.ts");
    return { ...chatModule, state, exchange: lines };
}

type Chat = Awaited<ReturnType<typeof freshChat>>;

// A view is opaque to core, so a case tells two apart by identity.
function newView(): FeatureView {
    return () => ({});
}

// A person's line channel as the mesh hands it over, which a case opens and reads from.
class FakeChannel {
    readyState = "open";
    sent: string[] = [];
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onmessage: ((event: { data: unknown }) => void) | null = null;

    send(data: string) {
        this.sent.push(data);
    }

    receive(data: unknown) {
        this.onmessage?.({ data: JSON.stringify(data) });
    }
}

function seatPerson(chat: Chat, participantId: string, name: string) {
    const channel = new FakeChannel();
    chat.state.namesByParticipant.set(participantId, name);
    chat.exchange.addPeer(participantId, channel);
    return channel;
}

// jsdom computes no layout: the list is as tall as it has children.
function registerList(chat: Chat, list: HTMLElement) {
    Object.defineProperty(list, "scrollHeight", { get: () => list.childElementCount });
    return chat.chatList(list);
}

afterEach(() => {
    vi.clearAllMocks();
    rendering.render.mockReset();
    document.body.innerHTML = "";
});

describe("opening and closing", () => {
    it("starts closed with nothing unread", async () => {
        const { chat } = await freshChat();

        expect(chat).toMatchObject({
            isOpen: false,
            unread: 0,
            draft: "",
            lines: [],
            pinned: null,
            card: null,
            readers: [],
        });
    });

    it("opens and closes without clearing the lines", async () => {
        const { chat, toggleChat, closeChat, receiveLine } = await freshChat();
        receiveLine(newView());

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
        chat.receiveLine(newView());
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

        receiveLine(newView());
        receiveLine(newView());

        expect(chat.unread).toBe(2);
        expect(sounds.playMessageSound).toHaveBeenCalledTimes(2);
    });

    it("neither counts nor sounds while the panel is open", async () => {
        const { chat, toggleChat, receiveLine } = await freshChat();
        toggleChat();

        receiveLine(newView());

        expect(chat.unread).toBe(0);
        expect(sounds.playMessageSound).not.toHaveBeenCalled();
    });
});

describe("sending", () => {
    it("shows the trimmed draft as an own line, delivered when no one else is in the call, and clears the draft", async () => {
        const { chat, sendChatMessage } = await freshChat();
        chat.draft = "  hello  ";

        sendChatMessage();

        expect(chat.lines).toEqual([{ kind: "self", id: 1, text: "hello", delivery: "delivered", missing: [] }]);
        expect(chat.draft).toBe("");
        expect(chat.unread).toBe(0);
    });

    it.each([
        ["empty", ""],
        ["blank", "   "],
        ["too long", "x".repeat(LINE_MAX_CHARACTERS + 1)],
        ["holding a control character", "two\nlines"],
    ])("keeps an %s draft and shows no line", async (_name, draft) => {
        const { chat, sendChatMessage } = await freshChat();
        chat.draft = draft;

        sendChatMessage();

        expect(chat.draft).toBe(draft);
        expect(chat.lines).toEqual([]);
    });

    it("sends on Enter and on no other key", async () => {
        const { chat, sendOnEnter } = await freshChat();
        chat.draft = "hello";

        sendOnEnter(new KeyboardEvent("keydown", { key: "a" }));
        expect(chat.lines).toEqual([]);
        sendOnEnter(new KeyboardEvent("keydown", { key: "Enter" }));

        expect(chat.lines).toHaveLength(1);
    });

    it("shows an own line pending until the person's receipt and then delivered", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");
        chat.chat.draft = "hello";

        chat.sendChatMessage();
        expect(chat.chat.lines[0]).toMatchObject({ delivery: "pending" });
        person.receive({ type: "receipt", id: 1 });

        expect(chat.chat.lines[0]).toMatchObject({ delivery: "delivered", missing: [] });
    });

    it("names the person a line did not reach once the person left before its receipt", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        seatPerson(chat, "p1", "Ada");
        chat.chat.draft = "hello";
        chat.sendChatMessage();

        chat.exchange.removePeer("p1");

        const [line] = chat.chat.lines;
        expect(line).toMatchObject({ delivery: "undelivered", missing: ["Ada"] });
        expect(chat.undeliveredNote(line as ChatLine)).toBe(words["chat.lineUndelivered"]({ names: "Ada" }));
    });

    it("shows no note under a line that is not an own one", async () => {
        const { undeliveredNote } = await freshChat();

        expect(undeliveredNote({ kind: "other", text: "hi", sender: "Ada" })).toBeNull();
    });

    it("shows nothing for the delivery of a line the chat does not hold", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        seatPerson(chat, "p1", "Ada");
        chat.exchange.send("not from the composer");

        chat.exchange.removePeer("p1");

        expect(chat.chat.lines).toEqual([]);
    });
});

describe("what the composer says", () => {
    it("says nothing for a draft that is a line", async () => {
        const { chat, draftProblem } = await freshChat();
        chat.draft = "x".repeat(LINE_MAX_CHARACTERS);

        expect(draftProblem()).toBeNull();
    });

    it.each([
        [
            "is over the limit",
            "x".repeat(LINE_MAX_CHARACTERS + 1),
            words["chat.lineTooLong"]({ max: String(LINE_MAX_CHARACTERS) }),
        ],
        ["holds a control character", "a\tb", words["chat.lineRefusedCharacter"]],
    ])("says why a draft that %s is not sent", async (_name, draft, expected) => {
        const { chat, draftProblem } = await freshChat();
        chat.draft = draft;

        expect(draftProblem()).toBe(expected);
    });

    it("names the agents that read along while some are shown, and none when the last left", async () => {
        const { chat, readersNote, setLineReaders } = await freshChat();
        expect(readersNote()).toBeNull();

        setLineReaders({ names: ["Claude", "Mozart"], frameType: "line_for_agents", send: () => {} });
        expect(readersNote()).toBe(words["chat.readersNote"]({ names: "Claude, Mozart" }));
        setLineReaders({ names: [], frameType: "line_for_agents", send: () => {} });

        expect(chat.readers).toEqual([]);
        expect(readersNote()).toBeNull();
    });
});

describe("receiving", () => {
    it("shows another person's line under the name of the person whose channel carried it and counts it", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");

        person.receive({ type: "line", id: 7, text: "hi" });

        expect(chat.chat.lines).toEqual([{ kind: "other", text: "hi", sender: "Ada" }]);
        expect(chat.chat.unread).toBe(1);
        expect(sounds.playMessageSound).toHaveBeenCalledOnce();
        expect(person.sent).toEqual([JSON.stringify({ type: "receipt", id: 7 })]);
    });

    it("shows a line of a person whose name is not known under no name", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");
        chat.state.namesByParticipant.clear();

        person.receive({ type: "line", id: 1, text: "hi" });

        expect(chat.chat.lines).toEqual([{ kind: "other", text: "hi", sender: "" }]);
    });
});

describe("feature views", () => {
    it("renders a view with the props it was bound to", async () => {
        const { featureView } = await freshChat();
        const shown: unknown[] = [];
        const internals = {} as ComponentInternals;

        featureView(
            (_internals: ComponentInternals, props: { text: string }) => {
                shown.push(props.text);
                return {};
            },
            { text: "bound" },
        )(internals, {});

        expect(shown).toEqual(["bound"]);
    });

    it("keeps a received view as a feature line", async () => {
        const { chat, receiveLine } = await freshChat();
        const view = newView();

        receiveLine(view);

        expect(chat.lines).toEqual([{ kind: "feature", view }]);
    });

    it("replaces and clears the pinned view", async () => {
        const { chat, pinLast } = await freshChat();
        const first = newView();
        const second = newView();

        pinLast(first);
        expect(chat.pinned).toBe(first);
        pinLast(second);
        expect(chat.pinned).toBe(second);
        pinLast(null);

        expect(chat.pinned).toBeNull();
    });

    it("replaces and clears the card", async () => {
        const { chat, showCard } = await freshChat();
        const first = newView();
        const second = newView();

        showCard(first);
        expect(chat.card).toBe(first);
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

        chat.receiveLine(newView());

        expect(list.childElementCount).toBe(1);
        expect(list.scrollTop).toBe(list.scrollHeight);
        stop();
    });

    it("scrolls after pinning a view and not after clearing it", async () => {
        const chat = await freshChat();
        const list = document.createElement("div");
        const stop = $effect.root(() => registerList(chat, list));
        list.append(document.createElement("div"));

        chat.pinLast(null);
        expect(list.scrollTop).toBe(0);
        chat.pinLast(newView());

        expect(list.scrollTop).toBe(list.scrollHeight);
        stop();
    });

    it("scrolls after showing a card and not after clearing it", async () => {
        const chat = await freshChat();
        const list = document.createElement("div");
        const stop = $effect.root(() => registerList(chat, list));
        list.append(document.createElement("div"));

        chat.showCard(null);
        expect(list.scrollTop).toBe(0);
        chat.showCard(newView());

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

        chat.receiveLine(newView());

        expect(list.scrollTop).toBe(0);
    });
});
