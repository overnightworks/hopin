import { describe, expect, it, vi } from "vitest";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import type { StagePort } from "./stage-port.ts";

type Frame = Record<string, unknown>;

const signal = vi.hoisted(() => ({ sent: [] as Frame[] }));

vi.mock("#lib/call/signaling.ts", () => ({
    on: () => undefined,
    send: (message: Frame) => {
        signal.sent.push(message);
    },
    isConnected: () => true,
}));

const peers = vi.hoisted(() => ({
    added: [] as { track: unknown; stream: unknown }[],
    removed: [] as unknown[],
    replaced: [] as unknown[],
}));

vi.mock("#lib/call/mesh.ts", () => ({
    addTrackToPeers: (track: unknown, stream: unknown) => {
        peers.added.push({ track, stream });
    },
    removeTrackFromPeers: (track: unknown) => {
        peers.removed.push(track);
    },
    replaceVideoTrackOnPeers: (track: unknown) => {
        peers.replaced.push(track);
        return Promise.resolve();
    },
}));

interface FakeTrack {
    kind: string;
    enabled: boolean;
    stopped: boolean;
    onended: (() => void) | null;
    stop(): void;
}

function track(kind: "audio" | "video"): FakeTrack {
    return {
        kind,
        enabled: true,
        stopped: false,
        onended: null,
        stop() {
            this.stopped = true;
        },
    };
}

function streamOf(...kinds: ("audio" | "video")[]) {
    const tracks = kinds.map(track);
    const stream = {
        tracks,
        getTracks: () => [...tracks],
        getVideoTracks: () => tracks.filter((candidate) => candidate.kind === "video"),
        getAudioTracks: () => tracks.filter((candidate) => candidate.kind === "audio"),
        addTrack: (added: FakeTrack) => {
            tracks.push(added);
        },
        removeTrack: (removed: FakeTrack) => {
            tracks.splice(tracks.indexOf(removed), 1);
        },
    };
    return stream;
}

type FakeStream = ReturnType<typeof streamOf>;
const asStream = (stream: FakeStream) => stream as unknown as MediaStream;

function rejection(name: string, message = name) {
    const error = new Error(message);
    error.name = name;
    return error;
}

interface HeldRequest {
    constraints: unknown;
    resolve(stream: FakeStream): void;
    reject(error: Error): void;
}

const requests: HeldRequest[] = [];

function held(constraints: unknown) {
    return new Promise<MediaStream>((resolve, reject) => {
        requests.push({
            constraints,
            resolve: (stream) => {
                resolve(asStream(stream));
            },
            reject,
        });
    });
}

async function settle() {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
}

async function freshMedia() {
    vi.resetModules();
    signal.sent.length = 0;
    peers.added.length = 0;
    peers.removed.length = 0;
    peers.replaced.length = 0;
    requests.length = 0;
    vi.stubGlobal("navigator", {
        mediaDevices: {
            getUserMedia: (constraints: unknown) => held(constraints),
            getDisplayMedia: (constraints: unknown) => held(constraints),
        },
    });
    const media = await import("./media.ts");
    const { installStage } = await import("./stage-port.ts");
    const { state } = await import("./state.ts");
    const stage = { showLocalStream: vi.fn(), updateLocalPreview: vi.fn() };
    installStage(stage as unknown as StagePort);
    return { ...media, state, stage };
}

type Fresh = Awaited<ReturnType<typeof freshMedia>>;

function mediaStates() {
    return signal.sent.filter((frame) => frame.type === SIGNAL_TYPE.MEDIA_STATE);
}

/** Starts the call with the camera and the microphone, so the controls have a stream. */
async function startedCall(fresh: Fresh) {
    const starting = fresh.acquireMedia();
    const own = streamOf("audio", "video");
    requests[0]?.resolve(own);
    await starting;
    signal.sent.length = 0;
    requests.length = 0;
    return own;
}

/** Starts a share from a started call and returns the screen's stream. */
async function sharing(fresh: Fresh, ...kinds: ("audio" | "video")[]) {
    const own = await startedCall(fresh);
    const onEnded = vi.fn();
    const starting = fresh.toggleScreenShare(onEnded);
    const screen = streamOf(...kinds);
    requests[0]?.resolve(screen);
    await starting;
    signal.sent.length = 0;
    requests.length = 0;
    peers.replaced.length = 0;
    vi.clearAllMocks();
    return { own, screen, onEnded };
}

describe("starting the media", () => {
    it("asks for the camera and the microphone together and starts with the camera on", async () => {
        const fresh = await freshMedia();
        const starting = fresh.acquireMedia();
        const own = streamOf("audio", "video");

        expect(requests.map((request) => request.constraints)).toEqual([{ video: true, audio: true }]);
        requests[0]?.resolve(own);
        await starting;

        expect(fresh.state.localStream).toBe(own);
        expect(fresh.state.videoEnabled).toBe(true);
    });

    it("falls back to the microphone alone with the camera off when the first request fails", async () => {
        const fresh = await freshMedia();
        const starting = fresh.acquireMedia();
        requests[0]?.reject(rejection("NotFoundError"));
        await settle();
        const own = streamOf("audio");
        expect(requests.map((request) => request.constraints)).toEqual([{ video: true, audio: true }, { audio: true }]);
        requests[1]?.resolve(own);
        await starting;

        expect(fresh.state.localStream).toBe(own);
        expect(fresh.state.videoEnabled).toBe(false);
    });

    it("throws the advice with the rejection as cause when both requests fail", async () => {
        const fresh = await freshMedia();
        const starting = fresh.acquireMedia();
        const refusal = rejection("NotAllowedError");
        requests[0]?.reject(rejection("NotFoundError"));
        await settle();
        requests[1]?.reject(refusal);

        await expect(starting).rejects.toMatchObject({
            message:
                "No camera or microphone available (NotAllowedError). Please connect a device or grant permission and try again.",
            cause: refusal,
        });
        expect(fresh.state.localStream).toBeNull();
    });

    it("stops a stream that arrives after the media ended and adopts nothing", async () => {
        const fresh = await freshMedia();
        const starting = fresh.acquireMedia();
        fresh.stopLocalMedia();
        const late = streamOf("audio", "video");
        requests[0]?.resolve(late);
        await starting;

        expect(late.tracks.every((candidate) => candidate.stopped)).toBe(true);
        expect(fresh.state.localStream).toBeNull();
    });

    it("does not ask for the microphone when the camera is refused after the media ended", async () => {
        const fresh = await freshMedia();
        const starting = fresh.acquireMedia();
        fresh.stopLocalMedia();
        requests[0]?.reject(rejection("NotAllowedError"));
        await starting;

        expect(requests).toHaveLength(1);
        expect(fresh.state.localStream).toBeNull();
    });
});

describe("muting", () => {
    it("flips the microphone and every audio track, refreshes the preview and announces", async () => {
        const fresh = await freshMedia();
        const own = await startedCall(fresh);

        fresh.toggleAudio();

        expect(fresh.state.audioEnabled).toBe(false);
        expect(own.getAudioTracks().map((candidate) => candidate.enabled)).toEqual([false]);
        expect(own.getVideoTracks().map((candidate) => candidate.enabled)).toEqual([true]);
        expect(fresh.stage.updateLocalPreview).toHaveBeenCalledOnce();
        expect(mediaStates()).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: false, video: true }]);
    });

    it("throws when there is no local stream", async () => {
        const fresh = await freshMedia();

        expect(() => {
            fresh.toggleAudio();
        }).toThrow("No local stream");
        expect(fresh.state.audioEnabled).toBe(true);
    });
});

describe("turning the camera on", () => {
    async function cameraOffCall(fresh: Fresh) {
        const own = await startedCall(fresh);
        fresh.releaseCamera();
        signal.sent.length = 0;
        peers.replaced.length = 0;
        return own;
    }

    it("replaces the old video track with the new one and hands it to the peers", async () => {
        const fresh = await freshMedia();
        const own = await startedCall(fresh);
        const oldTrack = own.getVideoTracks()[0];
        fresh.state.videoEnabled = false;
        const adding = fresh.addCamera();
        expect(requests.map((request) => request.constraints)).toEqual([{ video: true }]);
        const camera = streamOf("video");
        requests[0]?.resolve(camera);
        await adding;

        expect(own.getVideoTracks()).toEqual([camera.tracks[0]]);
        expect(oldTrack?.stopped).toBe(true);
        expect(fresh.stage.showLocalStream).toHaveBeenCalledWith(own);
        expect(peers.replaced).toEqual([camera.tracks[0]]);
        expect(fresh.state.videoEnabled).toBe(true);
        expect(fresh.stage.updateLocalPreview).toHaveBeenCalledOnce();
        expect(mediaStates()).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: true, video: true }]);
    });

    it("adds the track when the own stream has no video track", async () => {
        const fresh = await freshMedia();
        const starting = fresh.acquireMedia();
        requests[0]?.reject(rejection("NotFoundError"));
        await settle();
        const own = streamOf("audio");
        requests[1]?.resolve(own);
        await starting;
        requests.length = 0;
        const adding = fresh.addCamera();
        const camera = streamOf("video");
        requests[0]?.resolve(camera);
        await adding;

        expect(own.getVideoTracks()).toEqual([camera.tracks[0]]);
        expect(fresh.state.videoEnabled).toBe(true);
    });

    it("only records the choice during a share", async () => {
        const fresh = await freshMedia();
        await sharing(fresh, "video");
        fresh.state.videoEnabled = false;

        await fresh.addCamera();

        expect(requests).toEqual([]);
        expect(fresh.state.videoEnabled).toBe(true);
        expect(peers.replaced).toEqual([]);
        expect(mediaStates()).toHaveLength(1);
    });

    it("throws a CameraError carrying the cause's name and message when the request fails", async () => {
        const fresh = await freshMedia();
        await cameraOffCall(fresh);
        const adding = fresh.addCamera();
        requests[0]?.reject(rejection("NotFoundError", "no device"));

        await expect(adding).rejects.toMatchObject({
            name: "NotFoundError",
            message: "no device",
        });
        await expect(adding).rejects.toBeInstanceOf(fresh.CameraError);
    });

    it("reports nothing when an overtaken request fails", async () => {
        const fresh = await freshMedia();
        await cameraOffCall(fresh);
        const adding = fresh.addCamera();
        fresh.releaseCamera();
        requests[0]?.reject(rejection("NotFoundError"));

        await expect(adding).resolves.toBeUndefined();
    });

    it("stops the stream of a request that a newer press overtook", async () => {
        const fresh = await freshMedia();
        await cameraOffCall(fresh);
        const adding = fresh.addCamera();
        fresh.releaseCamera();
        const late = streamOf("video");
        requests[0]?.resolve(late);
        await adding;

        expect(late.tracks[0]?.stopped).toBe(true);
        expect(fresh.state.videoEnabled).toBe(false);
        expect(peers.replaced).toEqual([null]);
    });

    it("stops the stream of a request that the end of the call overtook", async () => {
        const fresh = await freshMedia();
        await cameraOffCall(fresh);
        const adding = fresh.addCamera();
        fresh.stopLocalMedia();
        const late = streamOf("video");
        requests[0]?.resolve(late);
        await adding;

        expect(late.tracks[0]?.stopped).toBe(true);
        expect(fresh.state.localStream).toBeNull();
    });

    it("throws when the camera stream carries no video track", async () => {
        const fresh = await freshMedia();
        const own = await startedCall(fresh);
        const oldTrack = own.getVideoTracks()[0];
        fresh.state.videoEnabled = false;
        const adding = fresh.addCamera();
        requests[0]?.resolve(streamOf("audio"));

        await expect(adding).rejects.toMatchObject({ message: "The stream carries no video track" });
        expect(oldTrack?.stopped).toBe(false);
        expect(own.getVideoTracks()).toEqual([oldTrack]);
    });
});

describe("turning the camera off", () => {
    it("removes and stops the camera track and sends no video to the peers", async () => {
        const fresh = await freshMedia();
        const own = await startedCall(fresh);
        const cameraTrack = own.getVideoTracks()[0];

        fresh.releaseCamera();

        expect(fresh.state.videoEnabled).toBe(false);
        expect(cameraTrack?.stopped).toBe(true);
        expect(own.getVideoTracks()).toEqual([]);
        expect(peers.replaced).toEqual([null]);
        expect(fresh.stage.updateLocalPreview).toHaveBeenCalledOnce();
        expect(mediaStates()).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: true, video: false }]);
    });

    it("keeps the share during a share", async () => {
        const fresh = await freshMedia();
        const { screen } = await sharing(fresh, "video");

        fresh.releaseCamera();

        expect(fresh.state.videoEnabled).toBe(false);
        expect(fresh.state.screenStream).toBe(screen);
        expect(screen.tracks[0]?.stopped).toBe(false);
        expect(peers.replaced).toEqual([]);
        expect(mediaStates()).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: true, video: true }]);
    });
});

describe("sharing the screen", () => {
    it("replaces the camera track with the screen's, stops the camera and ends through the callback", async () => {
        const fresh = await freshMedia();
        const own = await startedCall(fresh);
        const cameraTrack = own.getVideoTracks()[0];
        const onEnded = vi.fn();
        const starting = fresh.toggleScreenShare(onEnded);
        expect(requests.map((request) => request.constraints)).toEqual([{ video: true, audio: true }]);
        const screen = streamOf("video");
        requests[0]?.resolve(screen);
        await starting;

        expect(fresh.state.screenStream).toBe(screen);
        expect(own.getVideoTracks()).toEqual([screen.tracks[0]]);
        expect(cameraTrack?.stopped).toBe(true);
        expect(peers.replaced).toEqual([screen.tracks[0]]);
        expect(peers.added).toEqual([]);
        expect(mediaStates()).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: true, video: true }]);
        screen.tracks[0]?.onended?.();
        expect(onEnded).toHaveBeenCalledOnce();
    });

    it("hands the screen's audio to the peers under the screen's stream", async () => {
        const fresh = await freshMedia();
        const { screen } = await sharing(fresh, "video", "audio");

        expect(peers.added).toEqual([{ track: screen.tracks[1], stream: screen }]);
    });

    it("overtakes a pending camera request", async () => {
        const fresh = await freshMedia();
        await startedCall(fresh);
        fresh.releaseCamera();
        const adding = fresh.addCamera();
        const sharingNow = fresh.toggleScreenShare(vi.fn());
        const screen = streamOf("video");
        const lateCamera = streamOf("video");
        requests[1]?.resolve(screen);
        await sharingNow;
        requests[0]?.resolve(lateCamera);
        await adding;

        expect(lateCamera.tracks[0]?.stopped).toBe(true);
        expect(fresh.state.localStream?.getVideoTracks()).toEqual([screen.tracks[0]]);
    });

    it("stops a screen that arrives after the media ended", async () => {
        const fresh = await freshMedia();
        await startedCall(fresh);
        const starting = fresh.toggleScreenShare(vi.fn());
        fresh.stopLocalMedia();
        const late = streamOf("video", "audio");
        requests[0]?.resolve(late);
        await starting;

        expect(late.tracks.every((candidate) => candidate.stopped)).toBe(true);
        expect(fresh.state.screenStream).toBeNull();
        expect(peers.added).toEqual([]);
    });

    it("throws when the screen carries no video track", async () => {
        const fresh = await freshMedia();
        const own = await startedCall(fresh);
        const cameraTrack = own.getVideoTracks()[0];
        const starting = fresh.toggleScreenShare(vi.fn());
        requests[0]?.resolve(streamOf("audio"));

        await expect(starting).rejects.toMatchObject({ message: "The stream carries no video track" });
        expect(fresh.state.screenStream).toBeNull();
        expect(cameraTrack?.stopped).toBe(false);
        expect(own.getVideoTracks()).toEqual([cameraTrack]);
    });

    it("lets a second press join the pending change and refreshes once", async () => {
        const fresh = await freshMedia();
        await startedCall(fresh);
        const first = fresh.toggleScreenShare(vi.fn());
        const second = fresh.toggleScreenShare(vi.fn());
        requests[0]?.resolve(streamOf("video"));
        await Promise.all([first, second]);

        expect(second).toBe(first);
        expect(requests).toHaveLength(1);
        expect(fresh.stage.updateLocalPreview).toHaveBeenCalledOnce();
    });

    it("announces nothing and opens the picker again on the next press after a rejected picker", async () => {
        const fresh = await freshMedia();
        await startedCall(fresh);
        const first = fresh.toggleScreenShare(vi.fn());
        requests[0]?.reject(rejection("NotAllowedError"));

        await expect(first).rejects.toMatchObject({ name: "NotAllowedError" });
        expect(mediaStates()).toEqual([]);
        const next = fresh.toggleScreenShare(vi.fn());
        expect(requests).toHaveLength(2);
        requests[1]?.resolve(streamOf("video"));
        await next;
        expect(fresh.state.screenStream).not.toBeNull();
    });
});

describe("stopping the share", () => {
    it("sends no video and removes the screen's audio when the camera is off", async () => {
        const fresh = await freshMedia();
        const { screen } = await sharing(fresh, "video", "audio");
        fresh.state.videoEnabled = false;

        await fresh.toggleScreenShare(vi.fn());

        expect(peers.removed).toEqual([screen.tracks[1]]);
        expect(screen.tracks.every((candidate) => candidate.stopped)).toBe(true);
        expect(fresh.state.screenStream).toBeNull();
        expect(peers.replaced).toEqual([null]);
        expect(mediaStates()).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: true, video: false }]);
    });

    it("opens the camera again and replaces the screen's track when the camera is on", async () => {
        const fresh = await freshMedia();
        const { own, screen } = await sharing(fresh, "video");

        const stopping = fresh.toggleScreenShare(vi.fn());
        expect(requests.map((request) => request.constraints)).toEqual([{ video: true }]);
        const camera = streamOf("video");
        requests[0]?.resolve(camera);
        await stopping;

        expect(peers.removed).toEqual([]);
        expect(screen.tracks[0]?.stopped).toBe(true);
        expect(own.getVideoTracks()).toEqual([camera.tracks[0]]);
        expect(peers.replaced).toEqual([camera.tracks[0]]);
        expect(mediaStates()).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: true, video: true }]);
    });

    it("turns the camera off, sends no video and throws on when the camera cannot reopen", async () => {
        const fresh = await freshMedia();
        await sharing(fresh, "video");

        const stopping = fresh.toggleScreenShare(vi.fn());
        requests[0]?.reject(rejection("NotReadableError"));

        await expect(stopping).rejects.toBeInstanceOf(fresh.CameraError);
        expect(fresh.state.videoEnabled).toBe(false);
        expect(peers.replaced).toEqual([null]);
        expect(fresh.stage.updateLocalPreview).toHaveBeenCalledOnce();
        expect(mediaStates()).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: true, video: false }]);
    });
});

describe("ending the media", () => {
    it("stops and forgets the own stream and the screen", async () => {
        const fresh = await freshMedia();
        const { own, screen } = await sharing(fresh, "video", "audio");

        fresh.stopLocalMedia();

        expect([...own.tracks, ...screen.tracks].every((candidate) => candidate.stopped)).toBe(true);
        expect(fresh.state.localStream).toBeNull();
        expect(fresh.state.screenStream).toBeNull();
    });

    it("ends without streams", async () => {
        const fresh = await freshMedia();

        expect(() => {
            fresh.stopLocalMedia();
        }).not.toThrow();
    });
});
