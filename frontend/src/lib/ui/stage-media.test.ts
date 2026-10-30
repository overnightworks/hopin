// @vitest-environment jsdom
import { flushSync, mount, unmount } from "svelte";
import { describe, expect, it, vi } from "vitest";

import Fixture from "#lib/ui/stage-media.fixture.svelte";
import { showLocalStream } from "#lib/ui/stage.svelte.ts";

// The pure cases load fresh copies of the modules; the component case shares the test's own Svelte runtime.
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

describe("own picture action in a component", () => {
    it("assigns the stream once per delivery, the same stream again included", () => {
        const assigned = vi.fn();
        const component = mount(Fixture, { target: document.body });
        flushSync();
        Object.defineProperty(document.querySelector("video"), "srcObject", { set: assigned, configurable: true });
        const stream = someStream();
        expect(assigned).not.toHaveBeenCalled();

        showLocalStream(stream);
        flushSync();
        showLocalStream(stream);
        flushSync();

        expect(assigned).toHaveBeenCalledTimes(2);
        expect(assigned).toHaveBeenLastCalledWith(stream);
        void unmount(component);
    });
});
