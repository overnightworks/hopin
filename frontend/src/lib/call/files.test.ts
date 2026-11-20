import { describe, expect, it, vi } from "vitest";
import { DOWNLOAD_TYPE, FILE_DISPLAY, MEDIA_TYPE, PICTURE_TYPE } from "#lib/call/file.ts";
import {
    createFiles,
    FILE_PROBLEM,
    FILE_STATE,
    type FileChannel,
    type FileView,
    POST_REFUSAL,
} from "#lib/call/files.ts";
import { DELIVERY, type Delivery, type FileAnnouncement } from "#lib/call/lines.ts";
import { FILE_MAX_BYTES, FILE_MAX_RECEIVED_BYTES, PICTURE_AUTO_FETCH_BYTES } from "#lib/wire.ts";

const NAMES: Record<string, string> = { p1: "Ada", p2: "Ben", p3: "Cleo" };
const CHUNK = 16 * 1024;

const PNG_HEAD = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

function bytes(length: number, head: readonly number[] = []): Uint8Array {
    const content = new Uint8Array(length).fill(7);
    content.set(head.slice(0, length));
    return content;
}

function buffer(content: Uint8Array): ArrayBuffer {
    return content.buffer.slice(content.byteOffset, content.byteOffset + content.byteLength) as ArrayBuffer;
}

class FakeChannel implements FileChannel {
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
        return new Promise((resolve) => void this.sendWaiters.push({ count, resolve }));
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

class FakeTransport {
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

function exchange(options: { delivery?: Delivery; canPlay?: (type: string) => boolean } = {}) {
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
            acknowledge: (participantId, id) => void acknowledged.push([participantId, id]),
        },
        nameOf: (participantId) => NAMES[participantId] ?? "",
        createUrl: (blob) => {
            const url = `blob:${String(urls.size + 1)}`;
            urls.set(url, blob);
            return url;
        },
        revokeUrl: (url) => void revoked.push(url),
        canPlay: options.canPlay ?? (() => true),
    });
    files.listen({
        shown: (view) => void views.set(view.key, view),
        changed: (view) => void views.set(view.key, view),
    });
    const transport = new FakeTransport();
    files.addPeer("p1", transport);
    const view = (key: string) => views.get(key) as FileView;
    const blobOf = (key: string) => urls.get(view(key).url as string) as Blob;
    return { files, views, view, blobOf, urls, revoked, acknowledged, announcements, transport };
}

const PICTURE: FileAnnouncement = { name: "holiday.png", size: 3 * CHUNK + 5, mime: "image/png" };
const PAPER: FileAnnouncement = { name: "paper.pdf", size: 100, mime: "application/pdf" };

/** Everything a sender sends for a file of `size` bytes, as the chunks a receiver gets. */
function chunksOf(content: Uint8Array): ArrayBuffer[] {
    const chunks: ArrayBuffer[] = [];
    for (let offset = 0; offset < content.length; offset += CHUNK) {
        chunks.push(buffer(content.subarray(offset, offset + CHUNK)));
    }
    return chunks;
}

/** Follows the reads of a file's parts, so a case goes on when the exchange has read and not after a guess of how long that takes. */
function watchReads(file: Blob) {
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
            if (reads.length < count) await new Promise<void>((resolve) => void waiters.push({ count, resolve }));
            await reads[count - 1];
        },
    };
}

describe("a small picture announced", () => {
    it("is fetched at once on a channel of its own, shown when its first bytes are a raster picture, and answered once it is here", async () => {
        const { files, view, blobOf, acknowledged, transport } = exchange();
        const content = bytes(PICTURE.size, PNG_HEAD);

        files.announced("p1", 4, PICTURE);
        expect(transport.opened.map((channel) => channel.label)).toEqual(["file:4"]);
        expect(view("p1:4")).toMatchObject({ state: FILE_STATE.LOADING, name: "holiday.png", sender: "Ada" });
        expect(acknowledged).toEqual([]);
        const [channel] = transport.opened as [FakeChannel];
        expect(channel.binaryType).toBe("arraybuffer");
        for (const chunk of chunksOf(content)) channel.deliver(chunk);

        expect(view("p1:4")).toMatchObject({ state: FILE_STATE.DONE, display: FILE_DISPLAY.PICTURE });
        expect(blobOf("p1:4").type).toBe(PICTURE_TYPE.PNG);
        expect(new Uint8Array(await blobOf("p1:4").arrayBuffer())).toEqual(content);
        expect(acknowledged).toEqual([["p1", 4]]);
        expect(channel.readyState).toBe("closed");
    });

    it("is offered as a download of opaque bytes when its bytes are no raster picture, SVG included", () => {
        const { files, view, blobOf, transport } = exchange();
        const svg = new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>');

        files.announced("p1", 1, { name: "logo.png", size: svg.length, mime: "image/png" });
        (transport.opened[0] as FakeChannel).deliver(buffer(svg));

        expect(view("p1:1")).toMatchObject({ state: FILE_STATE.DONE, display: null });
        expect(blobOf("p1:1").type).toBe(DOWNLOAD_TYPE);
    });

    it("sees a picture whose first bytes arrive in pieces smaller than the check reads", () => {
        const { files, view, blobOf, transport } = exchange();
        const content = bytes(30, PNG_HEAD);

        files.announced("p1", 1, { name: "a.png", size: 30, mime: "image/png" });
        const [channel] = transport.opened as [FakeChannel];
        for (let offset = 0; offset < 30; offset += 3) channel.deliver(buffer(content.subarray(offset, offset + 3)));

        expect(view("p1:1").display).toBe(FILE_DISPLAY.PICTURE);
        expect(blobOf("p1:1").type).toBe(PICTURE_TYPE.PNG);
    });

    it("waits for a tap when it is above the size fetched at once", () => {
        const { files, view, acknowledged, transport } = exchange();

        files.announced("p1", 1, { ...PICTURE, size: PICTURE_AUTO_FETCH_BYTES + 1 });

        expect(transport.opened).toEqual([]);
        expect(view("p1:1").state).toBe(FILE_STATE.OFFERED);
        expect(acknowledged).toEqual([["p1", 1]]);
    });
});

describe("a song or video", () => {
    const MP4_HEAD = [0, 0, 0, 0x18, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d];
    const CLIP: FileAnnouncement = { name: "clip.mp4", size: 100, mime: "video/mp4" };

    function received(options: { canPlay?: (type: string) => boolean } = {}, announcement = CLIP, head = MP4_HEAD) {
        const side = exchange(options);
        side.files.announced("p1", 1, announcement);
        side.files.fetch("p1:1");
        (side.transport.opened[0] as FakeChannel).deliver(buffer(bytes(announcement.size, head)));
        return side;
    }

    it("waits for a tap, though it is small", () => {
        const { files, view, transport } = exchange();

        files.announced("p1", 1, CLIP);

        expect(transport.opened).toEqual([]);
        expect(view("p1:1").state).toBe(FILE_STATE.OFFERED);
    });

    it("is shown in a blob of the type its bytes show once the browser can decode it, whatever its sender calls it", () => {
        const { view, blobOf } = received({}, { ...CLIP, mime: "text/html" });

        expect(view("p1:1")).toMatchObject({ state: FILE_STATE.DONE, display: FILE_DISPLAY.VIDEO });
        expect(blobOf("p1:1").type).toBe(MEDIA_TYPE.MP4);
    });

    it("is offered as a download of opaque bytes when the browser cannot decode its type", () => {
        const decoded: string[] = [];
        const { view, blobOf } = received({
            canPlay: (type) => {
                decoded.push(type);
                return false;
            },
        });

        expect(decoded).toEqual([MEDIA_TYPE.MP4]);
        expect(view("p1:1")).toMatchObject({ state: FILE_STATE.DONE, display: null });
        expect(blobOf("p1:1").type).toBe(DOWNLOAD_TYPE);
    });

    it("is offered as a download of opaque bytes when its first bytes fail the container check, whatever its sender calls it", () => {
        const { view, blobOf } = received({}, CLIP, [0x25, 0x50, 0x44, 0x46]);

        expect(view("p1:1")).toMatchObject({ display: null });
        expect(blobOf("p1:1").type).toBe(DOWNLOAD_TYPE);
    });

    it("is offered as a download of opaque bytes once the element fails to decode it, and the player's URL is released", async () => {
        const { files, view, blobOf, revoked, urls } = received();
        const playerUrl = view("p1:1").url;

        files.undecodable("p1:1");

        expect(view("p1:1")).toMatchObject({ display: null });
        expect(view("p1:1").url).not.toBe(playerUrl);
        expect(revoked).toEqual([playerUrl]);
        expect(blobOf("p1:1").type).toBe(DOWNLOAD_TYPE);
        expect(new Uint8Array(await blobOf("p1:1").arrayBuffer())).toEqual(bytes(CLIP.size, MP4_HEAD));
        expect(urls.size).toBe(2);
    });

    it("keeps a picture a picture when its element reports an error, as only a song or video falls back", () => {
        const { files, view } = received({}, PICTURE, PNG_HEAD);

        files.undecodable("p1:1");

        expect(view("p1:1").display).toBe(FILE_DISPLAY.PICTURE);
    });

    it("has every blob URL released with the call, the one made after a failure included", () => {
        const { files, view, revoked } = received();
        files.undecodable("p1:1");
        const urlsHeld = [view("p1:1").url];

        files.release();

        expect(revoked.slice(1)).toEqual(urlsHeld);
        expect(view("p1:1").url).toBeNull();
    });

    it("is shown to its sender once its bytes pass the check and the browser can decode them, in a blob of the type its bytes show", async () => {
        const { files, view, blobOf } = exchange();

        files.post(new File([buffer(bytes(100, MP4_HEAD))], "clip.mp4", { type: "application/octet-stream" }));

        await vi.waitFor(() => {
            expect(view("own:1").display).toBe(FILE_DISPLAY.VIDEO);
        });
        expect(blobOf("own:1").type).toBe(MEDIA_TYPE.MP4);
    });

    it("is not shown to its sender when the browser cannot decode its type", async () => {
        const { files, view } = exchange({ canPlay: () => false });
        const clip = new File([buffer(bytes(100, MP4_HEAD))], "clip.mp4", { type: "video/mp4" });
        const reads = watchReads(clip);

        files.post(clip);
        await reads.read(1);

        expect(view("own:1")).toMatchObject({ display: null, url: null });
    });
});

describe("a file that waits for a tap", () => {
    it("is fetched on the tap and offered only as a download of opaque bytes whatever its sender calls it", async () => {
        const { files, view, blobOf, acknowledged, transport } = exchange();
        const content = bytes(PAPER.size);

        files.announced("p1", 2, { ...PAPER, mime: "text/html" });
        expect(acknowledged).toEqual([["p1", 2]]);
        files.fetch("p1:2");
        (transport.opened[0] as FakeChannel).deliver(buffer(content));

        expect(view("p1:2")).toMatchObject({ state: FILE_STATE.DONE, display: null });
        expect(blobOf("p1:2").type).toBe(DOWNLOAD_TYPE);
        expect(new Uint8Array(await blobOf("p1:2").arrayBuffer())).toEqual(content);
        expect(acknowledged).toEqual([["p1", 2]]);
    });

    it("is offered under a sanitized name", () => {
        const { files, view } = exchange();

        files.announced("p1", 1, { ...PAPER, name: "../..\\evil\u202efdp.exe" });

        expect(view("p1:1").name).toBe("....evilfdp.exe");
    });

    it("is fetched once when it is tapped twice", () => {
        const { files, transport } = exchange();

        files.announced("p1", 1, PAPER);
        files.fetch("p1:1");
        files.fetch("p1:1");
        files.fetch("p1:9");

        expect(transport.opened).toHaveLength(1);
    });
});

describe("the receiver's caps", () => {
    it("refuses a file over the per-file cap, never fetches it, and answers it", () => {
        const { files, view, acknowledged, transport } = exchange();

        files.announced("p1", 1, { ...PAPER, size: FILE_MAX_BYTES + 1 });
        files.fetch("p1:1");

        expect(view("p1:1").state).toBe(FILE_STATE.REFUSED);
        expect(transport.opened).toEqual([]);
        expect(acknowledged).toEqual([["p1", 1]]);
    });

    it("takes a file at the per-file cap", () => {
        const { files, view } = exchange();

        files.announced("p1", 1, { ...PAPER, size: FILE_MAX_BYTES });

        expect(view("p1:1").state).toBe(FILE_STATE.OFFERED);
    });

    it("shows nothing for an empty file and for an announcement repeated", () => {
        const { files, views, acknowledged } = exchange();

        files.announced("p1", 1, { ...PAPER, size: 0 });
        files.announced("p1", 2, PAPER);
        files.announced("p1", 2, { ...PAPER, name: "other.pdf" });

        expect([...views.keys()]).toEqual(["p1:2"]);
        expect(acknowledged).toEqual([["p1", 2]]);
    });

    it("refuses a second fetch from the same sender while one is in flight and takes it once the first is here", () => {
        const { files, view, transport } = exchange();

        files.announced("p1", 1, PAPER);
        files.announced("p1", 2, PAPER);
        files.fetch("p1:1");
        files.fetch("p1:2");
        expect(view("p1:2")).toMatchObject({ state: FILE_STATE.OFFERED, problem: FILE_PROBLEM.BUSY });
        (transport.opened[0] as FakeChannel).deliver(buffer(bytes(PAPER.size)));
        files.fetch("p1:2");

        expect(view("p1:2")).toMatchObject({ state: FILE_STATE.LOADING, problem: null });
        expect(transport.opened).toHaveLength(2);
    });

    it("leaves a small picture that comes while the sender's other file is in flight offered, and says why", () => {
        const { files, view, acknowledged, transport } = exchange();

        files.announced("p1", 1, PAPER);
        files.fetch("p1:1");
        files.announced("p1", 2, PICTURE);

        expect(view("p1:2")).toMatchObject({ state: FILE_STATE.OFFERED, problem: FILE_PROBLEM.BUSY });
        expect(transport.opened).toHaveLength(1);
        expect(acknowledged).toEqual([
            ["p1", 1],
            ["p1", 2],
        ]);
    });

    it("lets each sender have one file in flight and refuses a fetch beyond the total this page receives", () => {
        const { files, view } = exchange();
        const senders = Array.from(
            { length: FILE_MAX_RECEIVED_BYTES / FILE_MAX_BYTES + 1 },
            (_, index) => `s${String(index)}`,
        );
        for (const sender of senders) {
            files.addPeer(sender, new FakeTransport());
            files.announced(sender, 1, { ...PAPER, size: FILE_MAX_BYTES });
            files.fetch(`${sender}:1`);
        }

        const states = senders.map((sender) => [view(`${sender}:1`).state, view(`${sender}:1`).problem]);

        expect(states.slice(0, -1).every(([state]) => state === FILE_STATE.LOADING)).toBe(true);
        expect(states.at(-1)).toEqual([FILE_STATE.OFFERED, FILE_PROBLEM.TOTAL]);
    });

    it("takes back from the total what a discarded file did not bring", () => {
        const { files, view } = exchange();
        const senders = Array.from(
            { length: FILE_MAX_RECEIVED_BYTES / FILE_MAX_BYTES + 1 },
            (_, index) => `s${String(index)}`,
        );
        const transports = new Map<string, FakeTransport>();
        for (const sender of senders) {
            const transport = new FakeTransport();
            transports.set(sender, transport);
            files.addPeer(sender, transport);
            files.announced(sender, 1, { ...PAPER, size: FILE_MAX_BYTES });
        }
        for (const sender of senders.slice(0, -1)) files.fetch(`${sender}:1`);

        files.removePeer("s0");
        files.fetch(`${senders.at(-1) as string}:1`);

        expect(view(`${senders.at(-1) as string}:1`).state).toBe(FILE_STATE.LOADING);
    });
});

describe("a file that does not reach its end", () => {
    it("is discarded and shown incomplete when its sender leaves, and its channel is closed", () => {
        const { files, view, acknowledged, transport } = exchange();
        files.announced("p1", 1, PICTURE);
        const [channel] = transport.opened as [FakeChannel];
        channel.deliver(buffer(bytes(CHUNK, PNG_HEAD)));

        files.removePeer("p1");

        expect(view("p1:1")).toMatchObject({ state: FILE_STATE.INCOMPLETE, url: null });
        expect(channel.readyState).toBe("closed");
        expect(acknowledged).toEqual([["p1", 1]]);
    });

    it("is shown incomplete when its channel closes before its end while the sender stays", () => {
        const { files, view, transport } = exchange();
        files.announced("p1", 1, PICTURE);
        const [channel] = transport.opened as [FakeChannel];
        channel.deliver(buffer(bytes(CHUNK, PNG_HEAD)));

        channel.drop();
        channel.deliver(buffer(bytes(CHUNK)));

        expect(view("p1:1")).toMatchObject({ state: FILE_STATE.INCOMPLETE, url: null });
    });

    it("is discarded when more bytes come than the announcement named, or a message that is no bytes", () => {
        const { files, view, transport } = exchange();
        files.announced("p1", 1, { ...PAPER, size: 10 });
        files.announced("p1", 2, { ...PAPER, size: 10 });
        files.fetch("p1:1");
        (transport.opened[0] as FakeChannel).deliver(buffer(bytes(11)));
        files.fetch("p1:2");
        (transport.opened[1] as FakeChannel).deliver("text");

        expect([view("p1:1").state, view("p1:2").state]).toEqual([FILE_STATE.INCOMPLETE, FILE_STATE.INCOMPLETE]);
    });

    it("is discarded when a chunk is empty, so empty chunks are never kept", () => {
        const { files, view, transport } = exchange();
        files.announced("p1", 1, { ...PAPER, size: 10 });
        files.fetch("p1:1");
        const [channel] = transport.opened as [FakeChannel];

        channel.deliver(new ArrayBuffer(0));
        channel.deliver(new ArrayBuffer(0));

        expect(view("p1:1").state).toBe(FILE_STATE.INCOMPLETE);
        expect(channel.readyState).toBe("closed");
    });

    it("is gone when it waited for a tap and its sender left, and a file that is here stays", () => {
        const { files, view, transport } = exchange();
        files.announced("p1", 1, PAPER);
        files.announced("p1", 2, PAPER);
        files.fetch("p1:2");
        (transport.opened[0] as FakeChannel).deliver(buffer(bytes(PAPER.size)));

        files.removePeer("p1");
        files.fetch("p1:1");

        expect([view("p1:1").state, view("p1:2").state]).toEqual([FILE_STATE.INCOMPLETE, FILE_STATE.DONE]);
        expect(transport.opened).toHaveLength(1);
    });
});

describe("releasing the files", () => {
    it("revokes every blob URL, closes what is fetched, forgets what is held and counts again from nothing", () => {
        const { files, view, urls, revoked, transport } = exchange();
        files.announced("p1", 1, PAPER);
        files.fetch("p1:1");
        (transport.opened[0] as FakeChannel).deliver(buffer(bytes(PAPER.size)));
        files.announced("p1", 2, PICTURE);
        const [, loading] = transport.opened as [FakeChannel, FakeChannel];

        files.release();

        expect(revoked).toEqual([...urls.keys()]);
        expect(revoked).toHaveLength(1);
        expect([view("p1:1"), view("p1:2")].map((each) => [each.state, each.url])).toEqual([
            [FILE_STATE.INCOMPLETE, null],
            [FILE_STATE.INCOMPLETE, null],
        ]);
        expect(loading.readyState).toBe("closed");
    });
});

describe("a file posted", () => {
    function post(content: Uint8Array, name = "holiday.png", type = "image/png") {
        return new File([buffer(content)], name, { type });
    }

    it("is announced under its sanitized name and shown at once, and a picture shows once its bytes pass the check", async () => {
        const { files, view, blobOf, announcements } = exchange();

        const refusal = files.post(post(bytes(200, PNG_HEAD), "../holiday.png"));

        expect(refusal).toBeNull();
        expect(announcements).toEqual([{ name: "..holiday.png", size: 200, mime: "image/png" }]);
        expect(view("own:1")).toMatchObject({ own: true, state: FILE_STATE.DONE, delivery: DELIVERY.PENDING });
        await vi.waitFor(() => {
            expect(view("own:1").display).toBe(FILE_DISPLAY.PICTURE);
        });
        expect(blobOf("own:1").type).toBe(PICTURE_TYPE.PNG);
    });

    it("shows no picture for a file whose bytes are no raster picture", async () => {
        const { files, view, urls } = exchange();
        const svg = post(new TextEncoder().encode("<svg/>"), "logo.svg", "image/svg+xml");
        const reads = watchReads(svg);

        files.post(svg);
        await reads.read(1);

        expect(view("own:1")).toMatchObject({ display: null, url: null });
        expect(urls.size).toBe(0);
    });

    it("is refused when it is empty or over the per-file cap, announced to no one", () => {
        const { files, announcements } = exchange();
        const huge = new File([], "huge.bin");
        Object.defineProperty(huge, "size", { value: FILE_MAX_BYTES + 1 });

        expect([files.post(post(bytes(0))), files.post(huge)]).toEqual([POST_REFUSAL.EMPTY, POST_REFUSAL.TOO_LARGE]);
        expect(announcements).toEqual([]);
    });

    it("is refused while the sender's previous file is on its way, and is posted once that one is delivered", () => {
        const { files, announcements } = exchange();

        files.post(post(bytes(10), "one.bin", ""));
        const second = files.post(post(bytes(10), "two.bin", ""));
        files.delivered(1, DELIVERY.DELIVERED, []);
        const third = files.post(post(bytes(10), "two.bin", ""));

        expect([second, third]).toEqual([POST_REFUSAL.BUSY, null]);
        expect(announcements.map((each) => each.name)).toEqual(["one.bin", "two.bin"]);
    });

    it("follows the delivery its lines exchange reports", () => {
        const { files, view } = exchange();
        files.post(post(bytes(10), "one.bin", ""));

        files.delivered(1, DELIVERY.UNDELIVERED, ["Ben"]);
        files.delivered(9, DELIVERY.DELIVERED, []);

        expect(view("own:1")).toMatchObject({ delivery: DELIVERY.UNDELIVERED, missing: ["Ben"] });
    });
});

describe("a file served", () => {
    function sender() {
        const exchanged = exchange({ delivery: DELIVERY.DELIVERED });
        const content = bytes(3 * CHUNK + 5, PNG_HEAD);
        const file = new File([buffer(content)], "holiday.png", { type: "image/png" });
        const reads = watchReads(file);
        exchanged.files.post(file);
        return { ...exchanged, content, reads };
    }

    type Files = ReturnType<typeof exchange>["files"];

    function fetchedBy(participantId: string, id: number, files: Files) {
        const channel = new FakeChannel(`file:${String(id)}`);
        files.serve(participantId, channel);
        return channel;
    }

    it("goes over the channel in chunks and leaves the closing to the receiver, which holds the last byte", async () => {
        const { files, content } = sender();

        const channel = fetchedBy("p1", 1, files);
        await channel.whenSent(4);

        expect(channel.readyState).toBe("open");
        expect(channel.sent.map((chunk) => chunk.byteLength)).toEqual([CHUNK, CHUNK, CHUNK, 5]);
        expect(new Uint8Array(await new Blob(channel.sent).arrayBuffer())).toEqual(content);
    });

    it("reads and sends nothing while the channel is still connecting and starts when it opens", async () => {
        const { files, reads } = sender();
        const channel = new FakeChannel("file:1");
        channel.readyState = "connecting";
        const readsBefore = reads.requested;

        files.serve("p1", channel);
        const readsWhileConnecting = reads.requested;
        const sentWhileConnecting = channel.sent.length;
        channel.readyState = "open";
        channel.onopen?.();
        await channel.whenSent(4);

        expect([readsWhileConnecting, sentWhileConnecting]).toEqual([readsBefore, 0]);
    });

    it("waits on the send buffer: it sends no more while the buffer is full and goes on when it is low again", async () => {
        const { files, reads } = sender();
        const channel = new FakeChannel("file:1");
        channel.bufferedAmount = 10 * 1024 * 1024;
        const readsBefore = reads.requested;

        files.serve("p1", channel);
        const readsWhileFull = reads.requested;
        const sentWhileFull = channel.sent.length;
        channel.drain();
        await channel.whenSent(1);

        expect([readsWhileFull, sentWhileFull]).toEqual([readsBefore, 0]);
        expect(channel.bufferedAmountLowThreshold).toBeGreaterThanOrEqual(0);
    });

    it("sends nothing of a chunk that was being read when the receiver's channel closed", async () => {
        const { files, reads } = sender();
        const channel = new FakeChannel("file:1");
        const readsBefore = reads.requested;
        const settle = reads.hold();

        files.serve("p1", channel);
        channel.drop();
        settle();
        await reads.read(readsBefore + 1);

        expect(channel.sent).toEqual([]);
    });

    it.each([
        [
            "a person leaves",
            (files: Files) => {
                files.removePeer("p1");
            },
        ],
        [
            "the call ends",
            (files: Files) => {
                files.release();
            },
        ],
    ])("closes the channel it is serving when %s", (_case, end) => {
        const { files } = sender();
        const channel = fetchedBy("p1", 1, files);

        end(files);

        expect(channel.readyState).toBe("closed");
    });

    it.each([
        ["a file it never held", "file:9", "p1"],
        ["a label that names no file", "chat", "p1"],
        ["a person it did not announce the file to", "file:1", "p2"],
    ])("closes the channel that asks for %s", (_case, label, participantId) => {
        const { files } = sender();
        const channel = new FakeChannel(label);

        files.serve(participantId, channel);

        expect(channel.readyState).toBe("closed");
        expect(channel.sent).toEqual([]);
    });

    it("sends a file to a person once, and one channel at a time to a person", async () => {
        const { files } = sender();
        files.post(new File([buffer(bytes(10))], "second.bin"));
        const first = fetchedBy("p1", 1, files);
        const concurrent = fetchedBy("p1", 2, files);
        await first.whenSent(4);
        first.drop();
        const again = fetchedBy("p1", 1, files);
        let next: FakeChannel | undefined;
        // The channel that ended frees the person's place once the sender's send loop has returned.
        await vi.waitFor(() => {
            next = fetchedBy("p1", 2, files);
            expect(next.readyState).toBe("open");
        });

        expect([concurrent.readyState, again.readyState, next?.readyState]).toEqual(["closed", "closed", "open"]);
    });

    it("keeps the sender's composer busy while a person fetches the file, though the announcement is answered, and frees it at the end", async () => {
        const { files } = sender();
        const second = () => files.post(new File([buffer(bytes(10))], "second.bin"));

        const channel = fetchedBy("p1", 1, files);
        const whileFetched = second();
        channel.drop();
        await vi.waitFor(() => {
            expect(second()).toBeNull();
        });

        expect(whileFetched).toBe(POST_REFUSAL.BUSY);
    });

    it("serves nothing to a person who left, and nothing after the release", () => {
        const { files } = sender();
        files.removePeer("p1");
        const afterLeaving = fetchedBy("p1", 1, files);
        files.addPeer("p3", new FakeTransport());
        files.release();
        const afterRelease = fetchedBy("p1", 1, files);

        expect([afterLeaving.readyState, afterRelease.readyState]).toEqual(["closed", "closed"]);
    });
});

describe("a file between two pages", () => {
    it("arrives byte for byte, the sender waiting on the buffer, and the sender's file is delivered once the receiver has it", async () => {
        const senderSide = exchange({ delivery: DELIVERY.PENDING });
        const receiverSide = exchange();
        receiverSide.transport.serveAt = (channel) => {
            senderSide.files.serve("p1", channel);
        };
        const content = bytes(10 * CHUNK + 3, PNG_HEAD);

        senderSide.files.post(new File([buffer(content)], "holiday.png", { type: "image/png" }));
        const announcement = senderSide.announcements[0] as FileAnnouncement;
        receiverSide.files.announced("p1", 1, announcement);
        const [fetching] = receiverSide.transport.opened as [FakeChannel];
        await vi.waitFor(() => {
            expect(receiverSide.view("p1:1").state).toBe(FILE_STATE.DONE);
        });

        expect(new Uint8Array(await receiverSide.blobOf("p1:1").arrayBuffer())).toEqual(content);
        expect(receiverSide.view("p1:1").display).toBe(FILE_DISPLAY.PICTURE);
        expect(receiverSide.acknowledged).toEqual([["p1", 1]]);
        expect(fetching.readyState).toBe("closed");
    });
});
