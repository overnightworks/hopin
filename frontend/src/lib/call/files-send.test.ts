import { describe, expect, it, vi } from "vitest";
import { FILE_DISPLAY, PICTURE_TYPE } from "#lib/call/file.ts";
import { FILE_STATE, POST_REFUSAL } from "#lib/call/files.ts";
import { DELIVERY, type FileAnnouncement } from "#lib/call/lines.ts";
import { FILE_MAX_BYTES } from "#lib/wire.ts";
import { buffer, bytes, CHUNK, exchange, FakeChannel, FakeTransport, PNG_HEAD, watchReads } from "./files.fixture.ts";

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
