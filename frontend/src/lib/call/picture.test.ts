// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { takePicture } from "./picture.ts";
import { state } from "./state.ts";

interface Frame {
    width: number;
    height: number;
}

const BOUNDS = { maxPixels: 1280, maxBytes: 3000, maxMilliseconds: 10_000 };

function cameraTrack(overrides: Partial<{ enabled: boolean; readyState: string }> = {}) {
    return { kind: "video", enabled: true, readyState: "live", ...overrides };
}

function localStreamOf(...tracks: object[]) {
    return { getVideoTracks: () => tracks } as unknown as MediaStream;
}

// A canvas that encodes a picture of as many base64 characters as the quality asks for.
function installDevices(frame: Frame, encodedLengths: Record<number, number>) {
    const drawn: { width: number; height: number }[] = [];
    const video = {
        muted: false,
        playsInline: false,
        srcObject: null as unknown,
        videoWidth: frame.width,
        videoHeight: frame.height,
        readyState: HTMLMediaElement.HAVE_CURRENT_DATA,
        play: vi.fn().mockResolvedValue(undefined),
        pause: vi.fn(),
        addEventListener: vi.fn(),
    };
    const canvas = {
        width: 0,
        height: 0,
        getContext: () => ({
            drawImage: (_source: unknown, _x: number, _y: number, width: number, height: number) => {
                drawn.push({ width, height });
            },
        }),
        toDataURL: (type: string, quality: number) => `data:${type};base64,${"A".repeat(encodedLengths[quality] ?? 4)}`,
    };
    // Only the video and the canvas of a picture are created; either name of them takes the one fake of its kind.
    const made: Record<string, unknown> = { video, canvas };
    vi.spyOn(document, "createElement").mockImplementation(((name: string) => made[name]) as never);
    return { video, canvas, drawn };
}

beforeEach(() => {
    vi.stubGlobal(
        "MediaStream",
        class {
            constructor(readonly tracks: unknown[]) {}
        },
    );
    state.inCall = true;
    state.videoEnabled = true;
    state.screenStream = null;
    state.localStream = localStreamOf(cameraTrack());
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    state.inCall = false;
    state.localStream = null;
});

describe("the picture of the camera", () => {
    it.each([
        ["the person is not in a call", () => (state.inCall = false)],
        ["the camera is off", () => (state.videoEnabled = false)],
        ["a screen share runs", () => (state.screenStream = {} as MediaStream)],
        ["there is no stream", () => (state.localStream = null)],
        ["the stream carries no video track", () => (state.localStream = localStreamOf())],
        ["the track is disabled", () => (state.localStream = localStreamOf(cameraTrack({ enabled: false })))],
        ["the track has ended", () => (state.localStream = localStreamOf(cameraTrack({ readyState: "ended" })))],
    ])("is none when %s", async (_cause, arrange) => {
        const devices = installDevices({ width: 640, height: 480 }, {});
        arrange();

        expect(await takePicture(BOUNDS)).toBeNull();
        expect(devices.video.play).not.toHaveBeenCalled();
    });

    it.each([
        ["scaled down to the bound on its longer side", { width: 1920, height: 1080 }, { width: 1280, height: 720 }],
        ["a portrait frame the same", { width: 1080, height: 1920 }, { width: 720, height: 1280 }],
        ["left alone below the bound", { width: 640, height: 480 }, { width: 640, height: 480 }],
    ])("draws a frame %s", async (_name, frame, expected) => {
        const devices = installDevices(frame, {});

        await takePicture(BOUNDS);

        expect(devices.drawn).toEqual([expected]);
        expect([devices.canvas.width, devices.canvas.height]).toEqual([expected.width, expected.height]);
    });

    it("is the best quality whose bytes stay within the bound", async () => {
        // 4 base64 characters carry 3 bytes: 5600 characters are 4200 bytes, 3600 are 2700.
        installDevices({ width: 640, height: 480 }, { 0.85: 5600, 0.7: 4400, 0.5: 3600, 0.3: 1200 });

        const picture = await takePicture(BOUNDS);

        expect(picture).toBe("A".repeat(3600));
    });

    it("is the last quality's picture when none stays within the bound", async () => {
        installDevices({ width: 640, height: 480 }, { 0.85: 9000, 0.7: 8000, 0.5: 7000, 0.3: 6000 });

        expect(await takePicture(BOUNDS)).toBe("A".repeat(6000));
    });

    it("waits for the first frame of a camera that has none yet", async () => {
        const devices = installDevices({ width: 640, height: 480 }, {});
        devices.video.videoWidth = 0;
        devices.video.addEventListener.mockImplementation((_event: string, listener: () => void) => {
            devices.video.videoWidth = 640;
            listener();
        });

        expect(await takePicture(BOUNDS)).toBe("AAAA");
    });

    it("is none when the canvas gives no drawing context, and stops the playing element", async () => {
        const devices = installDevices({ width: 640, height: 480 }, {});
        devices.canvas.getContext = () => null as never;

        expect(await takePicture(BOUNDS)).toBeNull();
        expect(devices.video.srcObject).toBeNull();
    });

    it("stops the playing element, holding the camera's track no longer", async () => {
        const devices = installDevices({ width: 640, height: 480 }, {});

        await takePicture(BOUNDS);

        expect(devices.video.pause).toHaveBeenCalled();
        expect(devices.video.srcObject).toBeNull();
    });

    it("stops the playing element when the camera cannot play", async () => {
        const devices = installDevices({ width: 640, height: 480 }, {});
        devices.video.play.mockRejectedValue(new Error("blocked"));

        await expect(takePicture(BOUNDS)).rejects.toThrow("blocked");
        expect(devices.video.srcObject).toBeNull();
    });

    describe("a frame that never arrives", () => {
        beforeEach(() => {
            vi.useFakeTimers();
        });
        afterEach(() => {
            vi.useRealTimers();
        });

        it.each([
            [
                "the camera never plays",
                (video: { play: ReturnType<typeof vi.fn> }) => video.play.mockReturnValue(new Promise(() => {})),
            ],
            [
                "the camera plays but gives no frame",
                (video: { videoWidth: number }) => {
                    video.videoWidth = 0;
                },
            ],
        ])("is none and stops the playing element within the bound when %s", async (_cause, arrange) => {
            const devices = installDevices({ width: 640, height: 480 }, {});
            arrange(devices.video);

            const taken = takePicture(BOUNDS);
            await vi.advanceTimersByTimeAsync(BOUNDS.maxMilliseconds);

            expect(await taken).toBeNull();
            expect(devices.video.pause).toHaveBeenCalled();
            expect(devices.video.srcObject).toBeNull();
        });

        it("keeps waiting until the bound has passed", async () => {
            const devices = installDevices({ width: 640, height: 480 }, {});
            devices.video.play.mockReturnValue(new Promise(() => {}));

            void takePicture(BOUNDS);
            await vi.advanceTimersByTimeAsync(BOUNDS.maxMilliseconds - 1);

            expect(devices.video.pause).not.toHaveBeenCalled();
            expect(devices.video.srcObject).not.toBeNull();
        });
    });
});
