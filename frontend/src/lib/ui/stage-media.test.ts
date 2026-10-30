// @vitest-environment jsdom
import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Fixture from "#lib/ui/stage-media.fixture.svelte";
import Tiles from "#lib/ui/Tiles.svelte";
import {
    addRemoteTile,
    removeRemoteTile,
    setConnectionQuality,
    setRemoteSpeaking,
    showLocalStream,
    showRemoteStream,
} from "#lib/ui/stage.svelte.ts";

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

        showLocalStream(stream);
        flushSync();
        showLocalStream(stream);
        flushSync();

        expect(assigned).toHaveBeenCalledTimes(2);
        expect(assigned).toHaveBeenLastCalledWith(stream);
        void unmount(component);
    });
});

class FakeStream {
    constructor(private readonly tracks: MediaStreamTrack[] = []) {}
    getTracks() {
        return this.tracks;
    }
    getVideoTracks() {
        return this.tracks.filter((track) => track.kind === "video");
    }
}

function fakeTrack(kind: "audio" | "video", muted = false) {
    return Object.assign(new EventTarget(), { kind, muted, readyState: "live" }) as unknown as MediaStreamTrack;
}

function trackedAssignments() {
    const assigned = vi.fn();
    const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "srcObject");
    Object.defineProperty(HTMLMediaElement.prototype, "srcObject", {
        configurable: true,
        get(this: { current?: MediaStream }) {
            return this.current ?? null;
        },
        set(this: { current?: MediaStream }, value: MediaStream) {
            this.current = value;
            assigned(this, value);
        },
    });
    const restore = () => {
        if (descriptor) Object.defineProperty(HTMLMediaElement.prototype, "srcObject", descriptor);
        else delete (HTMLMediaElement.prototype as { srcObject?: unknown }).srcObject;
    };
    return { assigned, restore };
}

describe("remote tiles in a component", () => {
    let restore = () => {};
    let component: ReturnType<typeof mount> | undefined;
    const ids: string[] = [];

    beforeEach(() => {
        vi.stubGlobal("MediaStream", FakeStream);
    });

    afterEach(() => {
        if (component) void unmount(component);
        component = undefined;
        for (const id of ids.splice(0)) removeRemoteTile(id);
        restore();
        vi.unstubAllGlobals();
        document.body.innerHTML = "";
    });

    function mountTiles() {
        const tracking = trackedAssignments();
        restore = tracking.restore;
        component = mount(Tiles, { target: document.body });
        flushSync();
        return tracking.assigned;
    }

    function join(id: string) {
        ids.push(id);
        addRemoteTile(id);
        flushSync();
        return document.getElementById(`video-${id}`) as HTMLVideoElement;
    }

    it("renders the tile's elements with the video hidden and the placeholder shown", () => {
        mountTiles();

        join("a");

        expect(document.getElementById("video-a")?.classList.contains("hidden")).toBe(true);
        expect(document.getElementById("placeholder-a")?.classList.contains("hidden")).toBe(false);
        expect(document.querySelectorAll("#container-a .quality-bar")).toHaveLength(3);
    });

    it("assigns the voice once and the picture once, and nothing at a later speaking change", () => {
        const assigned = mountTiles();
        const video = join("a");
        const audio = fakeTrack("audio");
        const picture = fakeTrack("video", true);

        showRemoteStream("a", new FakeStream([audio, picture]) as unknown as MediaStream);
        flushSync();
        const voiceOnly = assigned.mock.calls.length;
        Object.defineProperty(picture, "muted", { value: false });
        picture.dispatchEvent(new Event("unmute"));
        setRemoteSpeaking("a", true);
        flushSync();

        expect([voiceOnly, assigned.mock.calls.length, (video.srcObject as unknown as FakeStream).getTracks()]).toEqual(
            [1, 2, [audio, picture]],
        );
    });

    it("assigns nothing when the same stream is delivered again with the same tracks", () => {
        const assigned = mountTiles();
        join("a");
        const stream = new FakeStream([fakeTrack("audio"), fakeTrack("video")]) as unknown as MediaStream;
        showRemoteStream("a", stream);
        flushSync();
        const before = assigned.mock.calls.length;

        showRemoteStream("a", stream);
        flushSync();

        expect(assigned).toHaveBeenCalledTimes(before);
    });

    it("attaches a stream delivered before the video mounted", () => {
        const assigned = mountTiles();
        ids.push("a");

        addRemoteTile("a");
        showRemoteStream("a", new FakeStream([fakeTrack("audio")]) as unknown as MediaStream);
        flushSync();

        expect(assigned).toHaveBeenCalledTimes(1);
    });

    it("keeps the first tile's video and its stream while others join and the middle one leaves", () => {
        const assigned = mountTiles();
        const first = join("a");
        showRemoteStream("a", new FakeStream([fakeTrack("audio")]) as unknown as MediaStream);
        flushSync();
        const stream = first.srcObject;
        const before = assigned.mock.calls.length;

        join("b");
        join("c");
        removeRemoteTile("b");
        flushSync();

        expect([
            document.getElementById("video-a") === first,
            first.srcObject === stream,
            assigned.mock.calls.length,
        ]).toEqual([true, true, before]);
    });

    it("does not re-attach on a mute, and re-attaches on an unmute", () => {
        const assigned = mountTiles();
        join("a");
        const picture = fakeTrack("video");
        showRemoteStream("a", new FakeStream([picture]) as unknown as MediaStream);
        flushSync();
        const before = assigned.mock.calls.length;

        picture.dispatchEvent(new Event("mute"));
        const afterMute = assigned.mock.calls.length;
        Object.defineProperty(picture, "muted", { value: true });
        picture.dispatchEvent(new Event("unmute"));

        expect([afterMute, assigned.mock.calls.length]).toEqual([before, before + 1]);
    });

    it("ignores an unmute for a tile that is gone", () => {
        const assigned = mountTiles();
        join("a");
        const picture = fakeTrack("video");
        showRemoteStream("a", new FakeStream([picture]) as unknown as MediaStream);
        flushSync();
        removeRemoteTile("a");
        flushSync();
        const before = assigned.mock.calls.length;

        Object.defineProperty(picture, "muted", { value: true });
        picture.dispatchEvent(new Event("unmute"));

        expect(assigned).toHaveBeenCalledTimes(before);
    });

    it("writes the quality on every report, the same value after its removal included", () => {
        mountTiles();
        join("a");
        const indicator = document.querySelector<HTMLElement>("#container-a .connection-quality");

        setConnectionQuality("a", "good");
        flushSync();
        indicator?.removeAttribute("data-quality");
        setConnectionQuality("a", "good");
        flushSync();

        expect(indicator?.dataset.quality).toBe("good");
    });

    it("paints a quality reported before the indicator mounted", () => {
        mountTiles();
        ids.push("a");
        addRemoteTile("a");
        setConnectionQuality("a", "poor");
        flushSync();

        expect(document.querySelector<HTMLElement>("#container-a .connection-quality")?.dataset.quality).toBe("poor");
    });
});
