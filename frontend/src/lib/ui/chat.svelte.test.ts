// @vitest-environment jsdom
import type { ComponentInternals } from "svelte";
import type { FileView } from "#lib/call/files.ts";
import type { ChatLine, FeatureView } from "#lib/ui/chat.svelte.ts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FILE_MAX_BYTES, FILE_MAX_RECEIVED_BYTES, LINE_MAX_CHARACTERS } from "#lib/wire.ts";
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
    const { files } = await import("#lib/call/files.ts");
    return { ...chatModule, state, exchange: lines, files };
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

const nativeBlobUrls = ["createObjectURL", "revokeObjectURL"].map((name) => [name, Reflect.get(URL, name)] as const);

// jsdom makes no blob URLs: a case names the one every file gets and reads back the ones released. The natives return in afterEach.
function fakeBlobUrls(url: string) {
    const revoked: string[] = [];
    URL.createObjectURL = () => url;
    URL.revokeObjectURL = (released) => void revoked.push(released);
    return revoked;
}

function megabytes(bytes: number): string {
    return String(bytes / (1024 * 1024));
}

afterEach(() => {
    for (const [name, native] of nativeBlobUrls) Reflect.set(URL, name, native);
    vi.restoreAllMocks();
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

// The way a person's file channels open, which a case reads what the page fetched over.
class FakeFileChannel {
    readyState = "open";
    bufferedAmount = 0;
    bufferedAmountLowThreshold = 0;
    binaryType = "blob";
    onopen = null;
    onclose: (() => void) | null = null;
    onmessage: ((event: { data: unknown }) => void) | null = null;
    onbufferedamountlow = null;

    constructor(readonly label: string) {}

    send() {}

    close() {
        this.readyState = "closed";
    }
}

function seatFileSender(chat: Chat, participantId: string) {
    const opened: FakeFileChannel[] = [];
    chat.files.addPeer(participantId, {
        open: (label) => {
            const channel = new FakeFileChannel(label);
            opened.push(channel);
            return channel;
        },
    });
    return opened;
}

const PAPER = { type: "file", id: 3, name: "paper.pdf", size: 4, mime: "application/pdf" };

describe("files", () => {
    it("shows a file announced under the sender's name, counts it as unread and answers it", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");

        person.receive(PAPER);

        expect(chat.chat.lines).toMatchObject([
            { kind: "file", file: { name: "paper.pdf", sender: "Ada", state: "offered", own: false } },
        ]);
        expect(chat.chat.unread).toBe(1);
        expect(person.sent).toEqual([JSON.stringify({ type: "receipt", id: 3 })]);
    });

    it("fetches a file on a tap and offers the bytes it got as a download under the file's name", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");
        const channels = seatFileSender(chat, "p1");
        fakeBlobUrls("blob:paper");
        const clicked: [string, string][] = [];
        vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
            clicked.push([this.href, this.download]);
        });
        person.receive(PAPER);
        const [{ file }] = chat.chat.lines as [{ kind: "file"; file: FileView }];

        chat.loadFile(file.key);
        channels[0]?.onmessage?.({ data: new Uint8Array(4).buffer });
        chat.downloadFile(file);

        expect(chat.fileNote(file)).toBeNull();
        expect(file).toMatchObject({ state: "done", url: "blob:paper", display: null });
        expect(clicked).toEqual([["blob:paper", "paper.pdf"]]);
    });

    it("plays a song the browser can decode and offers it as a download once the player fails on it", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");
        const channels = seatFileSender(chat, "p1");
        vi.spyOn(HTMLMediaElement.prototype, "canPlayType").mockReturnValue("probably");
        const revoked = fakeBlobUrls("blob:song");
        person.receive({ ...PAPER, name: "song.mp3", mime: "audio/mpeg" });
        const [{ file }] = chat.chat.lines as [{ kind: "file"; file: FileView }];
        chat.loadFile(file.key);
        channels[0]?.onmessage?.({ data: new Uint8Array([0x49, 0x44, 0x33, 3]).buffer });
        const display = file.display;

        chat.mediaUndecodable(file);

        expect([display, file.display]).toEqual(["audio", null]);
        expect(revoked).toEqual(["blob:song"]);
    });

    it("offers no download of a file whose bytes are not here", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");
        const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click");
        person.receive(PAPER);
        const [{ file }] = chat.chat.lines as [{ kind: "file"; file: FileView }];

        chat.downloadFile(file);

        expect(clicked).not.toHaveBeenCalled();
    });

    it("says what happened to a file that did not come", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");
        seatFileSender(chat, "p1");
        person.receive({ ...PAPER, size: FILE_MAX_BYTES + 1 });
        person.receive({ ...PAPER, id: 4 });
        person.receive({ ...PAPER, id: 5 });
        const [refused, waiting, other] = chat.chat.lines.map((line) => (line as { file: FileView }).file);

        chat.loadFile((waiting as FileView).key);
        chat.loadFile((other as FileView).key);
        chat.exchange.removePeer("p1");
        chat.files.removePeer("p1");

        expect(chat.fileNote(refused as FileView)).toBe(words["chat.fileRefused"]({ max: megabytes(FILE_MAX_BYTES) }));
        expect(chat.fileNote(waiting as FileView)).toBe(words["chat.fileIncomplete"]);
        expect(chat.fileNote(other as FileView)).toBe(words["chat.fileIncomplete"]);
    });

    it("says a file is loading while its bytes come, and that the sender's other file is still loading when a second one is tapped", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");
        seatFileSender(chat, "p1");
        person.receive(PAPER);
        person.receive({ ...PAPER, id: 4 });
        const [first, second] = chat.chat.lines.map((line) => (line as { file: FileView }).file);

        chat.loadFile((first as FileView).key);
        chat.loadFile((second as FileView).key);

        expect(chat.fileNote(first as FileView)).toBe(words["chat.fileLoading"]);
        expect(chat.fileNote(second as FileView)).toBe(words["chat.fileBusyReceiving"]({ name: "Ada" }));
    });

    it("says the page takes no more when a file would pass the total received in this call", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const senders = Array.from(
            { length: FILE_MAX_RECEIVED_BYTES / FILE_MAX_BYTES + 1 },
            (_, index) => `p${String(index)}`,
        );
        for (const sender of senders) {
            seatPerson(chat, sender, sender).receive({ ...PAPER, size: FILE_MAX_BYTES });
            seatFileSender(chat, sender);
        }
        const shown = chat.chat.lines.map((line) => (line as { file: FileView }).file);

        for (const file of shown) chat.loadFile(file.key);

        expect(chat.fileNote(shown.at(-1) as FileView)).toBe(
            words["chat.fileTotalReceived"]({ max: megabytes(FILE_MAX_RECEIVED_BYTES) }),
        );
        expect(chat.fileNote(shown[0] as FileView)).toBe(words["chat.fileLoading"]);
    });

    it.each([
        [1024, words["chat.fileSizeKilobytes"]({ size: "1" })],
        [3 * 1024 * 1024, words["chat.fileSizeMegabytes"]({ size: "3" })],
    ])("shows a file of %i bytes in the unit that fits", async (bytes, expected) => {
        const { fileSize } = await freshChat();

        expect(fileSize(bytes)).toBe(expected);
    });

    it("shows an own file pending until every receipt is in, then delivered, and names the person it did not reach", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const first = seatPerson(chat, "p1", "Ada");
        seatPerson(chat, "p2", "Ben");

        chat.postFile(new File(["abc"], "note.txt"));
        const [line] = chat.chat.lines as [ChatLine];
        expect(chat.isPending(line)).toBe(true);
        first.receive({ type: "receipt", id: 1 });
        chat.exchange.removePeer("p2");

        expect(line).toMatchObject({ kind: "file", file: { own: true, delivery: "undelivered", missing: ["Ben"] } });
        expect(chat.undeliveredNote(line)).toBe(words["chat.lineUndelivered"]({ names: "Ben" }));
        expect(chat.isPending(line)).toBe(false);
        expect(chat.chat.unread).toBe(0);
    });

    it.each([
        ["is empty", () => new File([], "none.txt"), words["chat.fileEmpty"]({ name: "none.txt" })],
        [
            "is over the per-file cap",
            () => Object.defineProperty(new File(["x"], "big.mov"), "size", { value: FILE_MAX_BYTES + 1 }),
            words["chat.fileTooLarge"]({ name: "big.mov", max: megabytes(FILE_MAX_BYTES) }),
        ],
    ])("names the file it does not post when it %s, and posts the next one", async (_name, make, expected) => {
        const chat = await freshChat();
        chat.registerChatHandlers();

        chat.postFile(make());
        expect(chat.unpostedNote()).toBe(expected);
        expect(chat.chat.lines).toEqual([]);
        chat.postFile(new File(["abc"], "fine.txt"));

        expect(chat.unpostedNote()).toBeNull();
        expect(chat.chat.lines).toHaveLength(1);
    });

    it("refuses a second file while the first is on its way", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        seatPerson(chat, "p1", "Ada");

        chat.postFile(new File(["abc"], "one.txt"));
        chat.postFile(new File(["abc"], "two.txt"));

        expect(chat.unpostedNote()).toBe(words["chat.fileBusy"]);
        expect(chat.chat.lines).toHaveLength(1);
    });

    it("hands a picked file to the composer's rules and lets the picker offer it again", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const input = document.createElement("input");
        input.type = "file";
        Object.defineProperty(input, "files", { value: [new File(["abc"], "pick.txt")] });
        const picked = vi.fn();
        input.addEventListener("click", picked);

        const stop = $effect.root(() => chat.chatFileInput(input));
        chat.pickFile();
        chat.filePicked({ currentTarget: input } as unknown as Event);
        stop();
        chat.pickFile();

        expect(picked).toHaveBeenCalledOnce();
        expect(chat.chat.lines).toHaveLength(1);
        expect(input.value).toBe("");
    });

    it("posts nothing when the picker is closed without a file", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const input = document.createElement("input");
        input.type = "file";

        chat.filePicked({ currentTarget: input } as unknown as Event);

        expect(chat.chat.lines).toEqual([]);
        expect(chat.unpostedNote()).toBeNull();
    });

    it("frees the blob URL of every file at the call's end", async () => {
        const chat = await freshChat();
        chat.registerChatHandlers();
        const person = seatPerson(chat, "p1", "Ada");
        const channels = seatFileSender(chat, "p1");
        const revoked = fakeBlobUrls("blob:paper");
        person.receive(PAPER);
        const [{ file }] = chat.chat.lines as [{ kind: "file"; file: FileView }];
        chat.loadFile(file.key);
        channels[0]?.onmessage?.({ data: new Uint8Array(4).buffer });

        chat.releaseChatFiles();

        expect(revoked).toEqual(["blob:paper"]);
        expect(file).toMatchObject({ state: "incomplete", url: null });
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
