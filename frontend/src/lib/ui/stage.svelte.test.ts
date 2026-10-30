// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

// The modules hold their state, so each case loads fresh copies.
async function freshStage() {
    vi.resetModules();
    const { state } = await import("#lib/call/state.ts");
    return { state, ...(await import("#lib/ui/stage.svelte.ts")) };
}

const someStream = () => new EventTarget() as MediaStream;

describe("own picture", () => {
    it("starts as the layout's markup shows it", async () => {
        const { ownPicture } = await freshStage();

        expect(ownPicture).toEqual({ stream: null, delivery: 0, mirrored: true, showsVideo: true, muted: false });
    });

    it("holds the stream and mirrors it when no screen is shared", async () => {
        const { ownPicture, showLocalStream } = await freshStage();
        const stream = someStream();

        showLocalStream(stream);

        expect(ownPicture.stream).toBe(stream);
        expect(ownPicture.mirrored).toBe(true);
    });

    it("does not mirror the stream while a screen is shared", async () => {
        const { ownPicture, showLocalStream, state } = await freshStage();
        state.screenStream = someStream();

        showLocalStream(someStream());

        expect(ownPicture.mirrored).toBe(false);
    });

    it("keeps the mirror it set until the next stream arrives", async () => {
        const { ownPicture, showLocalStream, state } = await freshStage();
        showLocalStream(someStream());

        state.screenStream = someStream();

        expect(ownPicture.mirrored).toBe(true);
    });

    it("counts a delivery for the same stream again", async () => {
        const { ownPicture, showLocalStream } = await freshStage();
        const stream = someStream();

        showLocalStream(stream);
        const first = ownPicture.delivery;
        showLocalStream(stream);

        expect(ownPicture.delivery).toBeGreaterThan(first);
    });

    it("clears the stream and still counts a delivery when the call is torn down", async () => {
        const { ownPicture, showLocalStream } = await freshStage();
        showLocalStream(someStream());
        const before = ownPicture.delivery;

        showLocalStream(null);

        expect(ownPicture.stream).toBeNull();
        expect(ownPicture.delivery).toBeGreaterThan(before);
    });

    it.each([true, false])("shows the video as the camera state %s says", async (videoEnabled) => {
        const { ownPicture, state, updateLocalPreview } = await freshStage();
        state.videoEnabled = videoEnabled;

        updateLocalPreview();

        expect(ownPicture.showsVideo).toBe(videoEnabled);
    });

    it.each([true, false])("marks the picture muted when the microphone state is %s", async (audioEnabled) => {
        const { ownPicture, state, updateLocalPreview } = await freshStage();
        state.audioEnabled = audioEnabled;

        updateLocalPreview();

        expect(ownPicture.muted).toBe(!audioEnabled);
    });
});
