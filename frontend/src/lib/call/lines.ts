// The one owner that sends a person's line to every recipient and reads the lines that arrive. A line reaches each
// person over the negotiated data channel the mesh makes with that person's peer connection, and the agents the page
// shows through the link a feature hands over, so the core names no agent. A line carries no sender: the page names it
// by the person whose channel carried it. Each receiver answers a line it shows with a receipt, which turns the
// sender's line from pending to delivered; a person who leaves, or whose channel closes, before its receipt turns it
// undelivered for that person. Every recipient is paced on its own, so a line never goes faster than the limits'
// budget, queued ones included. A person's receiver counts a line when it arrives, which may be much later than the
// line left, so the sender counts a line against the budget until its receipt is in and refills only from the receipt
// on: lines that were held up on the path and arrive together still find the receiver's budget.

import { cleanLine, LineBudget } from "#lib/call/line.ts";
import { isLive } from "#lib/call/signaling.ts";
import { state } from "#lib/call/state.ts";
import { LINE_BURST } from "#lib/wire.ts";

export const DELIVERY = {
    PENDING: "pending",
    DELIVERED: "delivered",
    UNDELIVERED: "undelivered",
} as const;
export type Delivery = (typeof DELIVERY)[keyof typeof DELIVERY];

/** The negotiated channel's id on both sides of a pair; each pair carries one line channel. */
export const LINE_CHANNEL_ID = 0;
export const LINE_CHANNEL_LABEL = "line";

const MESSAGE = {
    LINE: "line",
    RECEIPT: "receipt",
} as const;

// The agents' link has no receipts, so its sender cannot know when the server counts a line. It keeps one line of the
// burst unspent as a margin for the two clocks and charges the lines a resume writes again, which arrive together;
// a socket that stalls and then delivers everything at once is beyond it.
const AGENT_BURST = LINE_BURST - 1;

const CHANNEL_OPEN = "open";

/** The part of a data channel the exchange uses. */
export interface LineChannel {
    readonly readyState: string;
    send(data: string): void;
    onopen: ((event: Event) => void) | null;
    onclose: ((event: Event) => void) | null;
    onmessage: ((event: MessageEvent) => void) | null;
}

/** The agents the page shows as joined, by name, and how a line reaches them; the feature that knows them keeps it. */
export interface AgentLink {
    names: readonly string[];
    /** The type of the frame `send` writes: a resume writes a frame of it again when the server did not get it. */
    frameType: string;
    send: (text: string) => void;
}

export interface LineEvents {
    /** A line from the person whose channel carried it, once it passed the limits. */
    received: (sender: string, text: string) => void;
    /** The delivery of the line sent as `id` changed; `missing` names those it did not reach. */
    delivery: (id: number, delivery: Delivery, missing: readonly string[]) => void;
}

export interface SentLine {
    id: number;
    text: string;
    delivery: Delivery;
    missing: readonly string[];
}

export interface LineClock {
    now: () => number;
    /** Whether the page's session is live: a line for the agents waits while it is not. */
    isLive: () => boolean;
    nameOf: (participantId: string) => string;
}

interface Queued {
    id: number;
    text: string;
}

interface Peer {
    readonly channel: LineChannel;
    /** The receiver's budget as the sender knows it: debited when a receipt is in, never before. */
    readonly budget: LineBudget;
    readonly received: LineBudget;
    queue: Queued[];
    /** The lines sent whose receipt is not in; each is held against the budget. */
    unreceipted: Set<number>;
    timer: ReturnType<typeof setTimeout> | null;
    closed: boolean;
}

interface Pending {
    id: number;
    waiting: Set<string>;
    names: Map<string, string>;
    missing: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null;
}

export function createLines({ now, isLive, nameOf }: LineClock) {
    const peers = new Map<string, Peer>();
    const pending = new Map<number, Pending>();
    const agentBudget = new LineBudget(now, AGENT_BURST);
    let agentQueue: string[] = [];
    let agentTimer: ReturnType<typeof setTimeout> | null = null;
    let agents: AgentLink | null = null;
    let events: LineEvents = { received: () => {}, delivery: () => {} };
    let lastId = 0;

    function deliveryOf(line: Pending): Delivery {
        if (line.waiting.size > 0) return DELIVERY.PENDING;
        return line.missing.length > 0 ? DELIVERY.UNDELIVERED : DELIVERY.DELIVERED;
    }

    function settle(line: Pending, participantId: string, reached: boolean) {
        if (!line.waiting.delete(participantId)) return;
        if (!reached) line.missing.push(line.names.get(participantId) as string);
        events.delivery(line.id, deliveryOf(line), [...line.missing]);
        if (line.waiting.size === 0) pending.delete(line.id);
    }

    function settleAll(participantId: string) {
        for (const line of [...pending.values()]) settle(line, participantId, false);
    }

    function pace(peer: Peer) {
        if (peer.timer !== null) return;
        peer.timer = setTimeout(
            () => {
                peer.timer = null;
                drain(peer);
            },
            peer.budget.waitMilliseconds(peer.unreceipted.size + 1),
        );
    }

    function drain(peer: Peer) {
        if (peer.closed || peer.channel.readyState !== CHANNEL_OPEN) return;
        while (peer.queue.length > 0) {
            if (!peer.budget.holds(peer.unreceipted.size + 1)) {
                // A budget full of lines awaiting their receipts refills with the receipts, not with time.
                if (peer.unreceipted.size < LINE_BURST) pace(peer);
                return;
            }
            const { id, text } = peer.queue.shift() as Queued;
            peer.unreceipted.add(id);
            peer.channel.send(JSON.stringify({ type: MESSAGE.LINE, id, text }));
        }
    }

    function drainAgents() {
        if (!isLive()) return;
        while (agentQueue.length > 0) {
            if (!agentBudget.spend()) {
                if (agentTimer === null) {
                    agentTimer = setTimeout(() => {
                        agentTimer = null;
                        drainAgents();
                    }, agentBudget.waitMilliseconds());
                }
                return;
            }
            (agents as AgentLink).send(agentQueue.shift() as string);
        }
    }

    function receiveLine(participantId: string, peer: Peer, message: Record<string, unknown>) {
        if (!peer.received.spend()) return;
        const { id, text } = message;
        if (!Number.isSafeInteger(id) || typeof text !== "string") return;
        const line = cleanLine(text);
        if (line === null) return;
        events.received(participantId, line);
        peer.channel.send(JSON.stringify({ type: MESSAGE.RECEIPT, id }));
    }

    function receiveReceipt(participantId: string, peer: Peer, message: Record<string, unknown>) {
        const id = message.id as number;
        if (!peer.unreceipted.delete(id)) return;
        // The receiver counted the line before it answered, so the budget holds the line the sender counted for it.
        peer.budget.spend();
        settle(pending.get(id) as Pending, participantId, true);
        drain(peer);
    }

    function receive(participantId: string, peer: Peer, data: unknown) {
        if (typeof data !== "string") return;
        let message: unknown;
        try {
            message = JSON.parse(data);
        } catch {
            return;
        }
        if (!isRecord(message)) return;
        if (message.type === MESSAGE.LINE) receiveLine(participantId, peer, message);
        else if (message.type === MESSAGE.RECEIPT) receiveReceipt(participantId, peer, message);
    }

    function stop(peer: Peer) {
        if (peer.timer !== null) clearTimeout(peer.timer);
        peer.timer = null;
        peer.queue = [];
        peer.closed = true;
    }

    return {
        /** Take the events the exchange reports; the chat is the one listener. */
        listen(listener: LineEvents) {
            events = listener;
        },

        /** The person's line channel, made with its peer connection: the exchange sends the person's lines over it from its opening on. */
        addPeer(participantId: string, channel: LineChannel) {
            const peer: Peer = {
                channel,
                budget: new LineBudget(now),
                received: new LineBudget(now),
                queue: [],
                unreceipted: new Set(),
                timer: null,
                closed: false,
            };
            peers.set(participantId, peer);
            channel.onopen = () => {
                drain(peer);
            };
            channel.onclose = () => {
                stop(peer);
                settleAll(participantId);
            };
            channel.onmessage = (event) => {
                receive(participantId, peer, event.data);
            };
        },

        /** The person left the call: its queue is dropped and every line still waiting for it turns undelivered for it. */
        removePeer(participantId: string) {
            const peer = peers.get(participantId);
            if (peer === undefined) return;
            peers.delete(participantId);
            stop(peer);
            peer.channel.onopen = peer.channel.onclose = peer.channel.onmessage = null;
            settleAll(participantId);
        },

        /** The session turned live and wrote `replayed` again: those lines for the agents arrive at once, so they are charged to the budget before the lines that waited for the session go, paced. */
        resumeAgents(replayed: readonly { type: string }[]) {
            if (agents === null) return;
            agentBudget.charge(replayed.filter((frame) => frame.type === agents?.frameType).length);
            drainAgents();
        },

        /** The agents the page shows and how to reach them; null, or none named, when no agent is shown. */
        linkAgents(link: AgentLink | null) {
            agents = link !== null && link.names.length > 0 ? link : null;
            if (agents !== null) {
                drainAgents();
                return;
            }
            agentQueue = [];
            if (agentTimer !== null) clearTimeout(agentTimer);
            agentTimer = null;
        },

        /** Send the text to everyone the page shows; null when it is no line. */
        send(text: string): SentLine | null {
            const line = cleanLine(text);
            if (line === null) return null;
            lastId += 1;
            const sent: Pending = { id: lastId, waiting: new Set(), names: new Map(), missing: [] };
            for (const [participantId, peer] of peers) {
                const name = nameOf(participantId);
                sent.names.set(participantId, name);
                if (peer.closed) {
                    sent.missing.push(name);
                    continue;
                }
                sent.waiting.add(participantId);
                peer.queue.push({ id: sent.id, text: line });
            }
            if (sent.waiting.size > 0) pending.set(sent.id, sent);
            for (const peer of peers.values()) drain(peer);
            if (agents !== null) {
                agentQueue.push(line);
                drainAgents();
            }
            return { id: sent.id, text: line, delivery: deliveryOf(sent), missing: [...sent.missing] };
        },
    };
}

export const lines = createLines({
    now: () => performance.now(),
    isLive,
    nameOf: (participantId) => state.namesByParticipant.get(participantId) ?? "",
});
