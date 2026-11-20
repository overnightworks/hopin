import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type Delivery, type LineChannel, createLines } from "#lib/call/lines.ts";
import { LINE_BURST, LINE_MAX_CHARACTERS } from "#lib/wire.ts";

const SMILEY = "\u{1F600}";

class FakeChannel implements LineChannel {
    readyState: string;
    sent: unknown[] = [];
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    // The channel at the other end of the pair, when a case connects two exchanges.
    peer: FakeChannel | null = null;

    constructor(readyState = "open") {
        this.readyState = readyState;
    }

    send(data: string) {
        this.sent.push(JSON.parse(data));
        this.peer?.deliver(data);
    }

    deliver(data: unknown) {
        this.onmessage?.({ data } as MessageEvent);
    }

    open() {
        this.readyState = "open";
        this.onopen?.();
    }

    close() {
        this.readyState = "closed";
        this.onclose?.();
    }
}

const NAMES: Record<string, string> = { p1: "Ada", p2: "Ben", p3: "Cleo" };

function exchangeWithListener() {
    const lines = createLines({ now: () => Date.now(), nameOf: (id) => NAMES[id] ?? "" });
    const received: [string, string][] = [];
    const deliveries: [number, Delivery, readonly string[]][] = [];
    lines.listen({
        received: (sender, text) => void received.push([sender, text]),
        delivery: (id, delivery, missing) => void deliveries.push([id, delivery, missing]),
    });
    return { lines, received, deliveries };
}

// What each line shows now: the last delivery reported for it.
function shown(deliveries: readonly [number, Delivery, readonly string[]][]) {
    return Object.fromEntries(deliveries.map(([id, delivery, missing]) => [id, [delivery, missing]]));
}

function textsOf(channel: FakeChannel) {
    return channel.sent.map((message) => (message as { text: string }).text);
}

function receiptFor(id: number) {
    return JSON.stringify({ type: "receipt", id });
}

function lineMessage(id: number, text: string) {
    return JSON.stringify({ type: "line", id, text });
}

beforeEach(() => {
    vi.useFakeTimers();
});

afterEach(() => {
    vi.useRealTimers();
});

describe("a line to nobody", () => {
    it("shows as written and delivered, and nothing leaves the page", () => {
        const { lines } = exchangeWithListener();

        expect(lines.send("  hello ")).toEqual({ id: 1, text: "hello", delivery: "delivered", missing: [] });
    });

    it.each([
        ["blank", "  "],
        ["over the limit", "x".repeat(LINE_MAX_CHARACTERS + 1)],
        ["holding a control character", "a\nb"],
    ])("is no line when it is %s", (_name, text) => {
        const { lines } = exchangeWithListener();

        expect(lines.send(text)).toBeNull();
    });
});

describe("a line to people", () => {
    it("goes to each person over the channel and is delivered once every receipt is in", () => {
        const { lines, deliveries } = exchangeWithListener();
        const first = new FakeChannel();
        const second = new FakeChannel();
        lines.addPeer("p1", first);
        lines.addPeer("p2", second);

        const sent = lines.send("hello");
        first.deliver(receiptFor(1));
        expect(shown(deliveries)).toEqual({ 1: ["pending", []] });
        second.deliver(receiptFor(1));

        expect(sent?.delivery).toBe("pending");
        expect([textsOf(first), textsOf(second)]).toEqual([["hello"], ["hello"]]);
        expect(shown(deliveries)).toEqual({ 1: ["delivered", []] });
    });

    it("waits in the queue of a person whose channel is still connecting and goes at its opening", () => {
        const { lines, deliveries } = exchangeWithListener();
        const connecting = new FakeChannel("connecting");
        lines.addPeer("p1", connecting);

        lines.send("early");
        expect(connecting.sent).toEqual([]);
        connecting.open();
        connecting.deliver(receiptFor(1));

        expect(textsOf(connecting)).toEqual(["early"]);
        expect(deliveries).toEqual([[1, "delivered", []]]);
    });

    it("turns undelivered for a person who left before the receipt and goes on to the rest alone", () => {
        const { lines, deliveries } = exchangeWithListener();
        const staying = new FakeChannel();
        const leaving = new FakeChannel();
        lines.addPeer("p1", staying);
        lines.addPeer("p2", leaving);
        lines.send("first");
        staying.deliver(receiptFor(1));

        lines.removePeer("p2");
        const next = lines.send("second");
        staying.deliver(receiptFor(2));

        expect(shown(deliveries)).toEqual({ 1: ["undelivered", ["Ben"]], 2: ["delivered", []] });
        expect(next?.delivery).toBe("pending");
        expect(textsOf(leaving)).toEqual(["first"]);
    });

    it("drops the queue of a person who leaves and tells its lines undelivered", () => {
        const { lines, deliveries } = exchangeWithListener();
        const connecting = new FakeChannel("connecting");
        lines.addPeer("p1", connecting);
        lines.send("never sent");

        lines.removePeer("p1");
        connecting.open();

        expect(connecting.sent).toEqual([]);
        expect(deliveries).toEqual([[1, "undelivered", ["Ada"]]]);
    });

    it("shows this line and every later one undelivered for a person whose channel closed while the person stays", () => {
        const { lines, deliveries } = exchangeWithListener();
        const closing = new FakeChannel();
        const staying = new FakeChannel();
        lines.addPeer("p1", closing);
        lines.addPeer("p2", staying);
        lines.send("before");

        closing.close();
        const later = lines.send("after");
        staying.deliver(receiptFor(1));
        staying.deliver(receiptFor(2));

        expect(shown(deliveries)).toEqual({ 1: ["undelivered", ["Ada"]], 2: ["undelivered", ["Ada"]] });
        expect(later).toMatchObject({ delivery: "pending", missing: ["Ada"] });
    });

    it("stays pending while the path is down and is delivered once, when the receipt comes", () => {
        const { lines, deliveries } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);

        lines.send("through the outage");
        vi.advanceTimersByTime(60_000);
        expect(deliveries).toEqual([]);
        channel.deliver(receiptFor(1));
        channel.deliver(receiptFor(1));

        expect(deliveries).toEqual([[1, "delivered", []]]);
    });

    it("ignores a receipt for no line, from a person not waited on, and anything that is no message", () => {
        const { lines, deliveries } = exchangeWithListener();
        const first = new FakeChannel();
        const second = new FakeChannel();
        lines.addPeer("p1", first);
        lines.addPeer("p2", second);
        lines.send("hello");

        first.deliver(receiptFor(99));
        first.deliver("not json");
        first.deliver(JSON.stringify([1, 2]));
        first.deliver(JSON.stringify({ type: "other" }));
        first.deliver(new ArrayBuffer(1));

        expect(deliveries).toEqual([]);
    });
});

describe("pacing the sender's own lines", () => {
    it("sends a burst at once and then one line a second, never dropping one", () => {
        const { lines } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);

        for (let number = 1; number <= LINE_BURST + 5; number++) lines.send(`line ${String(number)}`);
        const atOnce = channel.sent.length;
        vi.advanceTimersByTime(1000);
        const afterASecond = channel.sent.length;
        vi.advanceTimersByTime(10_000);

        expect([atOnce, afterASecond]).toEqual([LINE_BURST - 1, LINE_BURST]);
        expect(textsOf(channel)).toEqual(
            Array.from({ length: LINE_BURST + 5 }, (_, index) => `line ${String(index + 1)}`),
        );
    });

    it("keeps a line waiting for its turn pending", () => {
        const { lines, deliveries } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);
        for (let number = 1; number <= LINE_BURST; number++) lines.send(`line ${String(number)}`);

        for (let id = 1; id <= LINE_BURST - 1; id++) channel.deliver(receiptFor(id));

        expect(deliveries.map(([, delivery]) => delivery)).toEqual(
            Array.from({ length: LINE_BURST - 1 }, () => "delivered"),
        );
    });

    it("lets a receiver that counts lines with the same budget show every line of a paced sender", () => {
        const sender = exchangeWithListener();
        const receiver = exchangeWithListener();
        const toReceiver = new FakeChannel();
        const toSender = new FakeChannel();
        toReceiver.peer = toSender;
        toSender.peer = toReceiver;
        sender.lines.addPeer("p1", toReceiver);
        receiver.lines.addPeer("p2", toSender);

        for (let number = 1; number <= 30; number++) sender.lines.send(`line ${String(number)}`);
        vi.advanceTimersByTime(60_000);

        expect(receiver.received).toHaveLength(30);
        expect(sender.deliveries.at(-1)).toEqual([30, "delivered", []]);
    });
});

describe("a line that arrives", () => {
    it("is shown under the person whose channel carried it and answered with a receipt", () => {
        const { lines, received } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);

        channel.deliver(lineMessage(5, "  hi  "));

        expect(received).toEqual([["p1", "hi"]]);
        expect(channel.sent).toEqual([{ type: "receipt", id: 5 }]);
    });

    it("passes 600 astral characters and not 601", () => {
        const { lines, received } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);

        channel.deliver(lineMessage(1, SMILEY.repeat(LINE_MAX_CHARACTERS)));
        channel.deliver(lineMessage(2, SMILEY.repeat(LINE_MAX_CHARACTERS + 1)));

        expect(received).toHaveLength(1);
        expect(channel.sent).toEqual([{ type: "receipt", id: 1 }]);
    });

    it.each([
        ["over the limit", "x".repeat(LINE_MAX_CHARACTERS + 1)],
        ["holding a control character", "two\nlines"],
        ["blank", "   "],
    ])("is dropped without a receipt when it is %s", (_name, text) => {
        const { lines, received } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);

        channel.deliver(lineMessage(1, text));

        expect([received, channel.sent]).toEqual([[], []]);
    });

    it.each([
        ["no id", JSON.stringify({ type: "line", text: "hi" })],
        ["a text that is none", JSON.stringify({ type: "line", id: 1, text: 5 })],
    ])("is dropped without a receipt when it holds %s", (_name, data) => {
        const { lines, received } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);

        channel.deliver(data);

        expect([received, channel.sent]).toEqual([[], []]);
    });

    it("is dropped without a receipt when it comes beyond the budget, until the budget refills", () => {
        const { lines, received } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);

        for (let id = 1; id <= LINE_BURST + 1; id++) channel.deliver(lineMessage(id, `line ${String(id)}`));
        const shownAtOnce = received.length;
        vi.advanceTimersByTime(1000);
        channel.deliver(lineMessage(99, "after the refill"));

        expect(shownAtOnce).toBe(LINE_BURST);
        expect(received.at(-1)).toEqual(["p1", "after the refill"]);
        expect(channel.sent).toHaveLength(LINE_BURST + 1);
    });

    it("is taken from no person once the person left", () => {
        const { lines, received } = exchangeWithListener();
        const channel = new FakeChannel();
        lines.addPeer("p1", channel);
        lines.removePeer("p1");

        channel.deliver(lineMessage(1, "late"));

        expect(received).toEqual([]);
    });
});

describe("a line for the agents", () => {
    it("reaches the link once, only while it names an agent, and no agent otherwise", () => {
        const { lines } = exchangeWithListener();
        const agentLines: string[] = [];
        lines.send("nobody yet");

        lines.linkAgents({ names: ["Claude"], send: (text) => void agentLines.push(text) });
        lines.send("hello");
        lines.linkAgents({ names: [], send: (text) => void agentLines.push(text) });
        lines.send("nobody again");

        expect(agentLines).toEqual(["hello"]);
    });

    it("does not make a line wait for the agents: it is delivered when only people are to receive it", () => {
        const { lines } = exchangeWithListener();
        lines.linkAgents({ names: ["Claude"], send: () => {} });

        expect(lines.send("hello")?.delivery).toBe("delivered");
    });

    it("is paced like a person's, the rest dropped with the agents when they leave", () => {
        const { lines } = exchangeWithListener();
        const agentLines: string[] = [];
        lines.linkAgents({ names: ["Claude"], send: (text) => void agentLines.push(text) });

        for (let number = 1; number <= LINE_BURST + 5; number++) lines.send(`line ${String(number)}`);
        const atOnce = agentLines.length;
        lines.linkAgents(null);
        vi.advanceTimersByTime(60_000);

        expect([atOnce, agentLines.length]).toEqual([LINE_BURST - 1, LINE_BURST - 1]);
    });

    it("is paced to the end while the agents stay", () => {
        const { lines } = exchangeWithListener();
        const agentLines: string[] = [];
        lines.linkAgents({ names: ["Claude"], send: (text) => void agentLines.push(text) });

        for (let number = 1; number <= LINE_BURST + 5; number++) lines.send(`line ${String(number)}`);
        vi.advanceTimersByTime(60_000);

        expect(agentLines).toHaveLength(LINE_BURST + 5);
    });
});
