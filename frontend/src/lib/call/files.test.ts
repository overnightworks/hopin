import { describe, expect, it, vi } from "vitest";
import { DOWNLOAD_TYPE, FILE_DISPLAY, MEDIA_TYPE, PICTURE_TYPE } from "#lib/call/file.ts";
import { FILE_PROBLEM, FILE_STATE } from "#lib/call/files.ts";
import { type FileAnnouncement } from "#lib/call/lines.ts";
import { FILE_MAX_BYTES, FILE_MAX_RECEIVED_BYTES, PICTURE_AUTO_FETCH_BYTES } from "#lib/wire.ts";
import {
    buffer,
    bytes,
    CHUNK,
    chunksOf,
    exchange,
    FakeChannel,
    FakeTransport,
    PNG_HEAD,
    watchReads,
} from "./files.fixture.ts";

const PICTURE: FileAnnouncement = { name: "holiday.png", size: 3 * CHUNK + 5, mime: "image/png" };
const PAPER: FileAnnouncement = { name: "paper.pdf", size: 100, mime: "application/pdf" };

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
