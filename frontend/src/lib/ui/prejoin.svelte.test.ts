// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const media = vi.hoisted(() => ({
    acquireMedia: vi.fn(),
    addCamera: vi.fn(),
    releaseCamera: vi.fn(),
    toggleAudio: vi.fn(),
}));
const stagePort = vi.hoisted(() => ({ showLocalStream: vi.fn(), updateLocalPreview: vi.fn() }));
const navigation = vi.hoisted(() => ({ goHome: vi.fn() }));
vi.mock("#lib/call/media.ts", () => media);
vi.mock("#lib/call/stage-port.ts", () => stagePort);
vi.mock("#lib/ui/navigation.ts", () => navigation);

// The modules hold their state, so each case loads fresh copies.
async function freshPrejoin() {
    vi.resetModules();
    const { state } = await import("#lib/call/state.ts");
    const { screens } = await import("#lib/ui/screens.svelte.ts");
    const prejoinModule = await import("#lib/ui/prejoin.svelte.ts");
    return { ...prejoinModule, state, screens };
}

const aStream = () => ({}) as MediaStream;

function deferred() {
    let settle: { resolve: () => void; reject: (reason: Error) => void } | null = null;
    const promise = new Promise<void>((resolve, reject) => {
        settle = { resolve, reject };
    });
    return { promise, resolve: () => settle?.resolve(), reject: (reason: Error) => settle?.reject(reason) };
}

beforeEach(() => {
    vi.resetAllMocks();
});

describe("before it is opened", () => {
    it("starts like the static markup: disabled, not pressed, the heading HopIn, no texts", async () => {
        const { isPrejoinInert, prejoin, prejoinTexts, startLabel } = await freshPrejoin();

        expect(prejoinTexts()).toEqual({ title: "", heading: "HopIn", subLine: "" });
        expect(startLabel()).toBe("");
        expect(isPrejoinInert()).toBe(true);
        expect(prejoin).toMatchObject({ isMicrophoneOn: true, isCameraOn: true, failureMessage: "" });
    });

    it("refuses a start before it is opened", async () => {
        const { installPrejoinStart, startClicked } = await freshPrejoin();
        installPrejoinStart(vi.fn());

        expect(startClicked).toThrow("not open");
    });

    it("refuses a start while no callback is installed", async () => {
        const { openPrejoin, startClicked } = await freshPrejoin();
        media.acquireMedia.mockResolvedValue(undefined);
        openPrejoin("host");

        expect(startClicked).toThrow("not open");
    });

    it("installs the start callback only once", async () => {
        const { installPrejoinStart } = await freshPrejoin();
        installPrejoinStart(vi.fn());

        expect(() => {
            installPrejoinStart(vi.fn());
        }).toThrow("already installed");
    });
});

describe.each([
    {
        kind: "host" as const,
        title: "New call",
        heading: "Ready to start?",
        subLine: "You'll get a link to invite people.",
        labels: {
            both: "Start with camera and mic",
            cameraOnly: "Start with camera only",
            micOnly: "Start with mic only",
            neither: "Start with camera and mic off",
        },
    },
    {
        kind: "guest" as const,
        title: "Join call",
        heading: "Ready to join?",
        subLine: "",
        labels: {
            both: "Join with camera and mic",
            cameraOnly: "Join with camera only",
            micOnly: "Join with mic only",
            neither: "Join with camera and mic off",
        },
    },
])("the $kind pre-join", ({ kind, title, heading, subLine, labels }) => {
    it("shows its texts and the pre-join screen, and waits for the media", async () => {
        const { isPrejoinInert, openPrejoin, prejoinTexts, screens, startLabel, state } = await freshPrejoin();
        const request = deferred();
        media.acquireMedia.mockReturnValue(request.promise);

        openPrejoin(kind);

        expect(prejoinTexts()).toEqual({ title, heading, subLine });
        expect(screens.current).toBe("prejoin");
        expect(isPrejoinInert()).toBe(true);
        state.localStream = aStream();
        request.resolve();
        await vi.waitFor(() => {
            expect(isPrejoinInert()).toBe(false);
        });
        expect(startLabel()).toBe(labels.both);
        expect(stagePort.showLocalStream).toHaveBeenCalledWith(state.localStream);
        expect(stagePort.updateLocalPreview).toHaveBeenCalled();
    });

    it.each([
        { cameraOn: true, microphoneOn: true, label: labels.both },
        { cameraOn: true, microphoneOn: false, label: labels.cameraOnly },
        { cameraOn: false, microphoneOn: true, label: labels.micOnly },
        { cameraOn: false, microphoneOn: false, label: labels.neither },
    ])("labels the start camera $cameraOn, microphone $microphoneOn", async ({ cameraOn, microphoneOn, label }) => {
        const { microphoneClicked, openPrejoin, startLabel, state } = await freshPrejoin();
        media.acquireMedia.mockResolvedValue(undefined);
        openPrejoin(kind);
        state.localStream = aStream();
        state.videoEnabled = cameraOn;
        state.audioEnabled = microphoneOn;
        media.toggleAudio.mockImplementation(() => undefined);

        microphoneClicked();

        expect(startLabel()).toBe(label);
    });
});

describe("a refused media request", () => {
    it("shows the message and leaves every control disabled", async () => {
        const { isPrejoinInert, openPrejoin, prejoin } = await freshPrejoin();
        media.acquireMedia.mockRejectedValue(new Error("Camera and microphone are blocked"));

        openPrejoin("host");
        await vi.waitFor(() => {
            expect(prejoin.failureMessage).toBe("Camera and microphone are blocked");
        });

        expect(isPrejoinInert()).toBe(true);
        expect(prejoin.isMediaPending).toBe(false);
    });
});

describe("the microphone", () => {
    it("toggles and redraws from the call state", async () => {
        const { microphoneClicked, prejoin, state } = await freshPrejoin();
        media.toggleAudio.mockImplementation(() => {
            state.audioEnabled = false;
        });
        expect(prejoin.isMicrophoneOn).toBe(true);

        microphoneClicked();

        expect(prejoin.isMicrophoneOn).toBe(false);
    });
});

describe("the camera", () => {
    it("is released when on, and redrawn", async () => {
        const { cameraClicked, prejoin, state } = await freshPrejoin();
        state.localStream = aStream();
        media.releaseCamera.mockImplementation(() => {
            state.videoEnabled = false;
        });
        expect(prejoin.isCameraOn).toBe(true);

        await cameraClicked();

        expect(prejoin.isCameraOn).toBe(false);
        expect(media.addCamera).not.toHaveBeenCalled();
    });

    it("is added when off, with every control waiting during the request", async () => {
        const { cameraClicked, isPrejoinInert, prejoin, state } = await freshPrejoin();
        state.localStream = aStream();
        state.videoEnabled = false;
        const request = deferred();
        media.addCamera.mockImplementation(() => {
            state.videoEnabled = true;
            return request.promise;
        });

        const clicked = cameraClicked();

        expect(prejoin.isMediaPending).toBe(true);
        expect(isPrejoinInert()).toBe(true);
        request.resolve();
        await clicked;
        expect(prejoin.isCameraOn).toBe(true);
        expect(isPrejoinInert()).toBe(false);
    });

    it("stays off and enabled when no camera answers", async () => {
        const { cameraClicked, isPrejoinInert, prejoin, state } = await freshPrejoin();
        state.localStream = aStream();
        state.videoEnabled = false;
        const request = deferred();
        media.addCamera.mockReturnValue(request.promise);

        const clicked = cameraClicked();
        expect(isPrejoinInert()).toBe(true);
        request.reject(new Error("no camera"));
        await clicked;

        expect(prejoin.isCameraOn).toBe(false);
        expect(isPrejoinInert()).toBe(false);
    });
});

describe("start", () => {
    it("reaches the installed callback with the open kind", async () => {
        const { installPrejoinStart, openPrejoin, startClicked } = await freshPrejoin();
        const onStart = vi.fn();
        installPrejoinStart(onStart);
        media.acquireMedia.mockResolvedValue(undefined);
        openPrejoin("guest");

        startClicked();

        expect(onStart).toHaveBeenCalledWith("guest");
    });
});
