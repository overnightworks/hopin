// What a files case runs against: channels and a transport that record what the exchange sends and let a case drive what arrives.
import { createFiles, type FileChannel, type FileView } from "#lib/call/files.ts";
import { DELIVERY, type Delivery, type FileAnnouncement } from "#lib/call/lines.ts";

export const NAMES: Record<string, string> = { p1: "Ada", p2: "Ben", p3: "Cleo" };
export const CHUNK = 16 * 1024;

export const PNG_HEAD = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

export function bytes(length: number, head: readonly number[] = []): Uint8Array {
    const content = new Uint8Array(length).fill(7);
    content.set(head.slice(0, length));
    return content;
}

export function buffer(content: Uint8Array): ArrayBuffer {
    return content.buffer.slice(content.byteOffset, content.byteOffset + content.byteLength) as ArrayBuffer;
}

export class FakeChannel implements FileChannel {
    readyState = "open";
    bufferedAmount = 0;
    bufferedAmountLowThreshold = 0;
    binaryType = "blob";
    sent: ArrayBuffer[] = [];
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onbufferedamountlow: (() => void) | null = null;
    // The channel at the other end of the pair, when a case connects two exchanges.
    peer: FakeChannel | null = null;
    readonly label: string;

    constructor(label: string) {
        this.label = label;
    }

    private readonly sendWaiters: { count: number; resolve: () => void }[] = [];

    send(data: ArrayBuffer) {
        this.sent.push(data);
        this.bufferedAmount += data.byteLength;
        this.peer?.deliver(data);
        for (const waiter of this.sendWaiters) if (this.sent.length >= waiter.count) waiter.resolve();
    }

    /** Settles once at least `count` messages are sent. */
    whenSent(count: number): Promise<void> {
        if (this.sent.length >= count) return Promise.resolve();
        return new Promise((resolve) => {
            this.sendWaiters.push({ count, resolve });
        });
    }

    deliver(data: unknown) {
        this.onmessage?.({ data } as MessageEvent);
    }

    /** The channel is closed from outside: the other end went away. */
    drop() {
        if (this.readyState === "closed") return;
        this.readyState = "closed";
        this.onclose?.();
    }

    close() {
        if (this.readyState === "closed") return;
        this.readyState = "closed";
        this.peer?.drop();
    }

    /** The send buffer empties and the channel says it is low. */
    drain() {
        this.bufferedAmount = 0;
        this.onbufferedamountlow?.();
    }
}

export class FakeTransport {
    opened: FakeChannel[] = [];
    // Called with the other end of each channel the receiver opens: the sender's serve.
    serveAt: ((channel: FakeChannel) => void) | null = null;

    open(label: string) {
        const channel = new FakeChannel(label);
        this.opened.push(channel);
        if (this.serveAt !== null) {
            const other = new FakeChannel(label);
            channel.peer = other;
            other.peer = channel;
            this.serveAt(other);
        }
        return channel;
    }
}

export function exchange(options: { delivery?: Delivery; canPlay?: (type: string) => boolean } = {}) {
    const views = new Map<string, FileView>();
    const urls = new Map<string, Blob>();
    const revoked: string[] = [];
    const acknowledged: [string, number][] = [];
    const announcements: FileAnnouncement[] = [];
    const files = createFiles({
        announcer: {
            announce: (announcement) => {
                announcements.push(announcement);
                return { id: announcements.length, delivery: options.delivery ?? DELIVERY.PENDING, missing: [] };
            },
            acknowledge: (participantId, id) => {
                acknowledged.push([participantId, id]);
            },
        },
        nameOf: (participantId) => NAMES[participantId] ?? "",
        createUrl: (blob) => {
            const url = `blob:${String(urls.size + 1)}`;
            urls.set(url, blob);
            return url;
        },
        revokeUrl: (url) => {
            revoked.push(url);
        },
        canPlay: options.canPlay ?? (() => true),
    });
    files.listen({
        shown: (view) => {
            views.set(view.key, view);
        },
        changed: (view) => {
            views.set(view.key, view);
        },
    });
    const transport = new FakeTransport();
    files.addPeer("p1", transport);
    const view = (key: string) => views.get(key) as FileView;
    const blobOf = (key: string) => urls.get(view(key).url as string) as Blob;
    return { files, views, view, blobOf, urls, revoked, acknowledged, announcements, transport };
}
/** Everything a sender sends for a file of `size` bytes, as the chunks a receiver gets. */
export function chunksOf(content: Uint8Array): ArrayBuffer[] {
    const chunks: ArrayBuffer[] = [];
    for (let offset = 0; offset < content.length; offset += CHUNK) {
        chunks.push(buffer(content.subarray(offset, offset + CHUNK)));
    }
    return chunks;
}

/** Follows the reads of a file's parts, so a case goes on when the exchange has read and not after a guess of how long that takes. */
export function watchReads(file: Blob) {
    const reads: Promise<ArrayBuffer>[] = [];
    let gate: Promise<void> = Promise.resolve();
    const waiters: { count: number; resolve: () => void }[] = [];
    const slice = file.slice.bind(file);
    file.slice = (...range: Parameters<Blob["slice"]>) => {
        const part = slice(...range);
        const read = part.arrayBuffer.bind(part);
        part.arrayBuffer = () => {
            const reading = gate.then(read);
            reads.push(reading);
            for (const waiter of waiters) if (reads.length >= waiter.count) waiter.resolve();
            return reading;
        };
        return part;
    };
    return {
        get requested() {
            return reads.length;
        },
        /** Keeps the reads asked for from now on pending until the returned function settles them. */
        hold() {
            let open = () => {};
            gate = new Promise<void>((resolve) => {
                open = resolve;
            });
            return open;
        },
        /** Settles when the `count`th part is read and the exchange went on with it. */
        async read(count: number) {
            if (reads.length < count)
                await new Promise<void>((resolve) => {
                    waiters.push({ count, resolve });
                });
            await reads[count - 1];
        },
    };
}
