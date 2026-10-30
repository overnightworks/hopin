// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

// The modules hold their state, so each case loads fresh copies.
async function freshStageMedia() {
    vi.resetModules();
    return { ...(await import("#lib/ui/stage-media.ts")), ...(await import("#lib/ui/stage.svelte.ts")) };
}

const someStream = () => new EventTarget() as MediaStream;

function videoWithSrcObjectSetter() {
    const video = document.createElement("video");
    const assigned = vi.fn();
    Object.defineProperty(video, "srcObject", { set: assigned, configurable: true });
    return { video, assigned };
}

describe("own picture action", () => {
    it("assigns nothing at mount", async () => {
        const { ownPictureMedia } = await freshStageMedia();
        const { video, assigned } = videoWithSrcObjectSetter();

        ownPictureMedia(video, 0);

        expect(assigned).not.toHaveBeenCalled();
    });

    it("hands the current stream to the video on update", async () => {
        const { ownPictureMedia, ownPicture } = await freshStageMedia();
        const { video, assigned } = videoWithSrcObjectSetter();
        ownPicture.stream = someStream();

        ownPictureMedia(video, 0).update();

        expect(assigned).toHaveBeenCalledExactlyOnceWith(ownPicture.stream);
    });
});
