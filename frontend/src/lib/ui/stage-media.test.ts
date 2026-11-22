// @vitest-environment jsdom
import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { screenMedia } from "#lib/ui/stage-media.ts";
import Fixture from "#lib/ui/stage-media.fixture.svelte";
import ScreenTiles from "#lib/ui/ScreenTiles.svelte";
import Tiles from "#lib/ui/Tiles.svelte";
import {
    addRemoteTile,
    removeRemoteTile,
    setConnectionQuality,
    setRemoteSpeaking,
    showAgentTile,
    showLocalStream,
    showRemoteScreen,
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

    it("does not re-attach on a mute", () => {
        const assigned = mountTiles();
        join("a");
        const picture = fakeTrack("video");
        showRemoteStream("a", new FakeStream([picture]) as unknown as MediaStream);
        flushSync();
        const before = assigned.mock.calls.length;

        Object.defineProperty(picture, "muted", { value: true });
        picture.dispatchEvent(new Event("mute"));

        expect(assigned.mock.calls).toHaveLength(before);
    });

    it("re-attaches the picture on an unmute", () => {
        const assigned = mountTiles();
        join("a");
        const picture = fakeTrack("video", true);
        showRemoteStream("a", new FakeStream([fakeTrack("audio"), picture]) as unknown as MediaStream);
        flushSync();
        const before = assigned.mock.calls.length;

        Object.defineProperty(picture, "muted", { value: false });
        picture.dispatchEvent(new Event("unmute"));

        expect(assigned.mock.calls).toHaveLength(before + 1);
        expect((assigned.mock.lastCall?.[1] as FakeStream).getTracks()).toContain(picture);
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

describe("shared screens in a component", () => {
    afterEach(() => {
        showRemoteScreen("a", null);
        vi.unstubAllGlobals();
        document.body.innerHTML = "";
    });

    it("renders a tile for a shared screen, plays its stream, and removes the tile when the share ends", () => {
        vi.stubGlobal("MediaStream", FakeStream);
        const tracking = trackedAssignments();
        const component = mount(ScreenTiles, { target: document.body });
        flushSync();
        expect(document.querySelector(".screens")).toBeNull();
        const audio = fakeTrack("audio");

        showRemoteScreen("a", new FakeStream([audio, fakeTrack("video", true)]) as unknown as MediaStream);
        flushSync();

        const video = document.getElementById("screen-video-a") as HTMLVideoElement;
        expect(video.srcObject).not.toBeNull();
        expect((video.srcObject as unknown as FakeStream).getTracks()).toEqual([audio]);

        showRemoteScreen("a", null);
        flushSync();

        expect(document.getElementById("screen-a")).toBeNull();
        tracking.restore();
        void unmount(component);
    });

    it("leaves the video alone when the share has ended before its update runs", () => {
        const video = document.createElement("video");

        screenMedia(video, { participantId: "a", delivery: 0 }).update();

        expect(video.srcObject).toBeFalsy();
    });

    it("plays a muted screen picture as soon as it unmutes, without a new delivery", () => {
        vi.stubGlobal("MediaStream", FakeStream);
        const tracking = trackedAssignments();
        const component = mount(ScreenTiles, { target: document.body });
        const audio = fakeTrack("audio");
        const picture = fakeTrack("video", true);
        showRemoteScreen("a", new FakeStream([audio, picture]) as unknown as MediaStream);
        flushSync();
        const video = document.getElementById("screen-video-a") as HTMLVideoElement;

        Object.defineProperty(picture, "muted", { value: false });
        picture.dispatchEvent(new Event("unmute"));
        flushSync();

        expect((video.srcObject as unknown as FakeStream).getTracks()).toEqual([audio, picture]);
        tracking.restore();
        void unmount(component);
    });

    it("plays the picture of a later share in its new tile when the same track unmutes there", () => {
        vi.stubGlobal("MediaStream", FakeStream);
        const tracking = trackedAssignments();
        const component = mount(ScreenTiles, { target: document.body });
        const picture = fakeTrack("video", true);
        showRemoteScreen("a", new FakeStream([picture]) as unknown as MediaStream);
        flushSync();
        showRemoteScreen("a", null);
        flushSync();
        showRemoteScreen("a", new FakeStream([picture]) as unknown as MediaStream);
        flushSync();
        const video = document.getElementById("screen-video-a") as HTMLVideoElement;

        Object.defineProperty(picture, "muted", { value: false });
        picture.dispatchEvent(new Event("unmute"));
        flushSync();

        expect((video.srcObject as unknown as FakeStream).getTracks()).toEqual([picture]);
        tracking.restore();
        void unmount(component);
    });

    it("takes the tracks of a second delivery of the screen stream", () => {
        vi.stubGlobal("MediaStream", FakeStream);
        const tracking = trackedAssignments();
        const component = mount(ScreenTiles, { target: document.body });
        const picture = fakeTrack("video", true);
        showRemoteScreen("a", new FakeStream([fakeTrack("audio"), picture]) as unknown as MediaStream);
        flushSync();
        const video = document.getElementById("screen-video-a") as HTMLVideoElement;
        const laterAudio = fakeTrack("audio");

        showRemoteScreen("a", new FakeStream([laterAudio, picture]) as unknown as MediaStream);
        flushSync();

        expect((video.srcObject as unknown as FakeStream).getTracks()).toEqual([laterAudio]);
        tracking.restore();
        void unmount(component);
    });
});

describe("an agent tile's chips in a component", () => {
    // A wide tile: jsdom lays nothing out, so its width is the one the test gives.
    beforeEach(() => {
        vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(400);
        vi.stubGlobal(
            "ResizeObserver",
            class {
                observe() {}
                unobserve() {}
                disconnect() {}
            },
        );
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        showAgentTile(null, "agent-a1");
        vi.restoreAllMocks();
        document.body.innerHTML = "";
    });

    const chips = [
        { switch: "hearing" as const, expected: [true, false] },
        { switch: "listening" as const, expected: [true, false] },
        { switch: "seeing" as const, expected: [true, false] },
    ];

    async function chipOf(chip: Pick<(typeof chips)[number], "switch">, figure: string | undefined) {
        showAgentTile(
            { name: "Ada", activity: "present", figure, [chip.switch]: { on: true, onChange: () => {} } } as never,
            "agent-a1",
        );
        const component = mount(Tiles, { target: document.body });
        await vi.waitFor(() => {
            expect(document.querySelector(".hear-set > *")).not.toBeNull();
        });
        flushSync();
        const compact = document.querySelector(".hear-set .compact") !== null;
        void unmount(component);
        return compact;
    }

    it.each(chips)(
        "CALL-77: makes the $switch chip compact as $expected for a tile that names a figure and one that does not",
        async (chip) => {
            const withFigure = await chipOf(chip, "/api/participation/figures/abc");
            document.body.innerHTML = "";
            const withoutFigure = await chipOf(chip, undefined);

            expect([withFigure, withoutFigure]).toEqual(chip.expected);
        },
    );

    it.each([
        { width: 184, expected: true },
        { width: 400, expected: false },
    ])(
        "CALL-79: makes the listen chip compact as $expected on a tile $width px wide, where its label would not fit whole",
        async ({ width, expected }) => {
            vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(width);

            expect(await chipOf({ switch: "listening" }, undefined)).toBe(expected);
        },
    );

    it("CALL-79: shows the listen switch on and hands a press to the tile's owner as the value it asks for", async () => {
        const onChange = vi.fn();
        showAgentTile({ name: "Ada", activity: "present", listening: { on: true, onChange } } as never, "agent-a1");
        const component = mount(Tiles, { target: document.body });
        await vi.waitFor(() => {
            expect(document.querySelector(".hear-set [role=switch]")).not.toBeNull();
        });
        flushSync();
        const listen = document.querySelector(".hear-set [role=switch]") as HTMLButtonElement;

        listen.click();

        expect([listen.getAttribute("aria-checked"), onChange.mock.calls]).toEqual(["true", [[false]]]);
        void unmount(component);
    });
});
