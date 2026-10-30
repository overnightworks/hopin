import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ICE_SERVERS_PATH, SIGNAL_TYPE } from "#lib/wire.ts";
import type { StagePort } from "./stage-port.ts";

type Frame = Record<string, unknown>;

const signal = vi.hoisted(() => ({
    handlers: new Map<string, ((frame: Frame) => void)[]>(),
    sent: [] as Frame[],
}));

vi.mock("#lib/call/signaling.ts", () => ({
    on: (type: string, handler: (frame: Frame) => void) => {
        signal.handlers.set(type, [...(signal.handlers.get(type) ?? []), handler]);
    },
    send: (message: Frame) => {
        signal.sent.push(message);
    },
    isConnected: () => true,
}));

class FakeSender {
    track: { kind: string } | null;
    readonly setStreams: ((stream: unknown) => void) | undefined = vi.fn();
    readonly replaceTrack = vi.fn((track: unknown) => Promise.resolve(track));
    readonly capped: unknown[] = [];

    constructor(track: { kind: string } | null) {
        this.track = track;
    }

    getParameters() {
        return { encodings: [{}] as { maxBitrate?: number }[] };
    }

    setParameters(parameters: { encodings: { maxBitrate?: number }[] }) {
        this.capped.push(parameters.encodings[0]?.maxBitrate);
        return Promise.resolve();
    }
}

interface FakeTransceiver {
    direction: string;
    receiver: { track: { kind: string } };
    sender: FakeSender;
}

class FakeConnection {
    static instances: FakeConnection[] = [];
    static offerGate: Promise<void> | null = null;
    signalingState = "stable";
    iceConnectionState = "new";
    connectionState = "connected";
    localDescription: Frame | null = null;
    remoteDescriptions: unknown[] = [];
    candidates: unknown[] = [];
    transceivers: FakeTransceiver[] = [];
    offerOptions: unknown[] = [];
    ontrack: ((event: { streams: unknown[] }) => void) | null = null;
    onicecandidate: ((event: { candidate: unknown }) => void) | null = null;
    closeAfterRemoteDescription = false;
    closeAfterAnswer = false;
    receivers: unknown[] = [];
    private readonly listeners = new Map<string, (() => void)[]>();

    constructor(readonly configuration: unknown) {
        FakeConnection.instances.push(this);
    }

    addTrack(track: { kind: string }) {
        this.transceivers.push({
            direction: "sendrecv",
            receiver: { track: { kind: track.kind } },
            sender: new FakeSender(track),
        });
    }

    addTransceiver(kind: string, init: { direction: string }) {
        this.transceivers.push({
            direction: init.direction,
            receiver: { track: { kind } },
            sender: new FakeSender(null),
        });
    }

    getTransceivers() {
        return this.transceivers;
    }

    getSenders() {
        return this.transceivers.map((transceiver) => transceiver.sender);
    }

    getReceivers() {
        return this.receivers;
    }

    getStats() {
        return Promise.resolve(
            new Map([["pair", { type: "candidate-pair", state: "succeeded", currentRoundTripTime: 0.1 }]]),
        );
    }

    removeTrack(sender: FakeSender) {
        sender.track = null;
    }

    async createOffer(options?: unknown) {
        await FakeConnection.offerGate;
        this.offerOptions.push(options);
        return { type: "offer", sdp: `offer-${String(this.offerOptions.length)}` };
    }

    createAnswer() {
        if (this.signalingState === "closed") return Promise.reject(new Error("connection closed"));
        if (this.closeAfterAnswer) this.signalingState = "closed";
        return Promise.resolve({ type: "answer", sdp: "answer" });
    }

    setLocalDescription(description: Frame) {
        this.localDescription = description;
        this.signalingState = description.type === "offer" ? "have-local-offer" : "stable";
        return Promise.resolve();
    }

    setRemoteDescription(description: unknown) {
        this.remoteDescriptions.push(description);
        if (this.closeAfterRemoteDescription) this.signalingState = "closed";
        else if ((description as Frame).type === "offer") this.signalingState = "have-remote-offer";
        else this.signalingState = "stable";
        return Promise.resolve();
    }

    addIceCandidate(candidate: unknown) {
        this.candidates.push(candidate);
        return Promise.resolve();
    }

    close() {
        this.signalingState = "closed";
    }

    addEventListener(type: string, listener: () => void) {
        this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
    }

    dispatch(type: string) {
        for (const listener of this.listeners.get(type) ?? []) listener();
    }
}

class FakeDescription {
    constructor(readonly init: Frame) {}
}

function track(kind: string) {
    return { kind } as unknown as MediaStreamTrack;
}

function streamWith(...kinds: string[]) {
    const tracks = kinds.map(track);
    return {
        getTracks: () => tracks,
        getVideoTracks: () => tracks.filter((candidate) => candidate.kind === "video"),
    } as unknown as MediaStream;
}

const reportError = vi.fn();
const iceServers = [{ urls: "stun:ice.test" }];

function respondToFetch(status: number, body: unknown = { iceServers }) {
    vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({ status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }),
    );
}

// The module holds its connections, so each case loads a fresh copy.
async function freshMesh({ watchPeers = true } = {}) {
    vi.resetModules();
    signal.handlers.clear();
    signal.sent.length = 0;
    FakeConnection.instances = [];
    FakeConnection.offerGate = null;
    const mesh = await import("./mesh.ts");
    const { installStage } = await import("./stage-port.ts");
    const { state } = await import("./state.ts");
    const stage = {
        addRemoteTile: vi.fn(),
        removeRemoteTile: vi.fn(() => true),
        showRemoteStream: vi.fn(),
        setConnectionQuality: vi.fn(),
        setRemoteSpeaking: vi.fn(),
    };
    installStage(stage as unknown as StagePort);
    mesh.registerMeshHandlers();
    const peersChanged = vi.fn();
    if (watchPeers) mesh.onPeersChanged(peersChanged);
    return { ...mesh, state, stage, peersChanged };
}

function deliver(type: SIGNAL_TYPE, frame: Frame = {}) {
    for (const handler of signal.handlers.get(type) ?? []) handler({ type, ...frame });
}

async function settle() {
    await vi.advanceTimersByTimeAsync(0);
}

function connectionOf(index = 0) {
    return FakeConnection.instances[index] as FakeConnection;
}

function sentOfType(type: SIGNAL_TYPE) {
    return signal.sent.filter((frame) => frame.type === type);
}

async function meetPeer(mesh: Awaited<ReturnType<typeof freshMesh>>, ...participants: string[]) {
    deliver(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participants });
    await settle();
    return participants.map((_, index) => connectionOf(index));
}

beforeEach(() => {
    vi.useFakeTimers();
    reportError.mockReset();
    vi.stubGlobal("RTCPeerConnection", FakeConnection);
    vi.stubGlobal("RTCSessionDescription", FakeDescription);
    vi.stubGlobal("RTCIceCandidate", FakeDescription);
    vi.stubGlobal("reportError", reportError);
    respondToFetch(200);
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

describe("loading the ice servers", () => {
    it("asks the ice servers path and gives every later connection the servers", async () => {
        const mesh = await freshMesh();

        await mesh.loadIceServers();
        await meetPeer(mesh, "p1");

        expect(vi.mocked(fetch)).toHaveBeenCalledWith(ICE_SERVERS_PATH);
        expect(connectionOf().configuration).toEqual({ iceServers });
    });

    it("throws SignedOutError on a 401", async () => {
        const mesh = await freshMesh();
        respondToFetch(401);

        await expect(mesh.loadIceServers()).rejects.toThrow(mesh.SignedOutError);
        await expect(mesh.loadIceServers()).rejects.toThrow("Signed out");
    });

    it("names the status of any other failure", async () => {
        const mesh = await freshMesh();
        respondToFetch(503);

        await expect(mesh.loadIceServers()).rejects.toThrow("Could not load ICE servers (503)");
    });

    it("connects with no servers until they are loaded", async () => {
        const mesh = await freshMesh();

        await meetPeer(mesh, "p1");

        expect(connectionOf().configuration).toEqual({ iceServers: [] });
    });
});

describe("meeting the people already there", () => {
    it("opens one connection per person, adds a tile and offers to each", async () => {
        const mesh = await freshMesh();

        await meetPeer(mesh, "p1", "p2");

        expect(mesh.peerCount()).toBe(2);
        expect(mesh.stage.addRemoteTile.mock.calls).toEqual([["p1"], ["p2"]]);
        expect(mesh.peersChanged).toHaveBeenCalledTimes(2);
        expect(sentOfType(SIGNAL_TYPE.OFFER).map((frame) => frame.target)).toEqual(["p1", "p2"]);
        expect(sentOfType(SIGNAL_TYPE.OFFER)[0]?.sdp).toBe(connectionOf().localDescription);
    });

    it("sends every local track and no extra transceiver when a camera track exists", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("audio", "video");

        await meetPeer(mesh, "p1");

        expect(connectionOf().transceivers.map((transceiver) => transceiver.receiver.track.kind)).toEqual([
            "audio",
            "video",
        ]);
    });

    it("adds a sendrecv video transceiver when the camera is missing", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("audio");

        await meetPeer(mesh, "p1");

        expect(
            connectionOf().transceivers.map((transceiver) => [transceiver.receiver.track.kind, transceiver.direction]),
        ).toEqual([
            ["audio", "sendrecv"],
            ["video", "sendrecv"],
        ]);
    });

    it("sends no tracks without a local stream", async () => {
        const mesh = await freshMesh();

        await meetPeer(mesh, "p1");

        expect(connectionOf().transceivers).toEqual([]);
    });

    it("caps the video upload of every connection", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("video");

        await meetPeer(mesh, "p1", "p2");

        expect(connectionOf(0).transceivers[0]?.sender.capped.at(-1)).toBe(1_000_000);
    });

    it("hands a remote stream to the stage", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        const stream = streamWith("video");

        connection?.ontrack?.({ streams: [stream] });

        expect(mesh.stage.showRemoteStream).toHaveBeenCalledWith("p1", stream);
    });

    it("leaves the tile as it is when a track names no stream", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");

        connection?.ontrack?.({ streams: [] });

        expect(mesh.stage.showRemoteStream).not.toHaveBeenCalled();
    });

    it("sends each ICE candidate to the person and skips the end-of-candidates event", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");

        connection?.onicecandidate?.({ candidate: { candidate: "c1" } });
        connection?.onicecandidate?.({ candidate: null });

        expect(sentOfType(SIGNAL_TYPE.ICE_CANDIDATE)).toEqual([
            { type: SIGNAL_TYPE.ICE_CANDIDATE, target: "p1", candidate: { candidate: "c1" } },
        ]);
    });

    it("starts the quality and speaking monitors only once the offer is made", async () => {
        const mesh = await freshMesh();
        let releaseOffer: () => void = () => undefined;
        FakeConnection.offerGate = new Promise<void>((release) => {
            releaseOffer = release;
        });
        await meetPeer(mesh, "p1");
        connectionOf().receivers = [
            {
                track: { kind: "audio" },
                getSynchronizationSources: () => [
                    { audioLevel: 0.5, timestamp: performance.timeOrigin + performance.now() },
                ],
            },
        ];

        await vi.advanceTimersByTimeAsync(3000);
        expect(mesh.stage.setConnectionQuality).not.toHaveBeenCalled();
        expect(mesh.stage.setRemoteSpeaking).not.toHaveBeenCalled();

        releaseOffer();
        await vi.advanceTimersByTimeAsync(3000);
        expect(mesh.stage.setConnectionQuality).toHaveBeenCalledWith("p1", "good");
        expect(mesh.stage.setRemoteSpeaking).toHaveBeenCalledWith("p1", true);
    });

    it("restarts ICE with an offer flagged iceRestart once the connection fails", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        deliver(SIGNAL_TYPE.ANSWER, { from: "p1", sdp: { type: "answer", sdp: "a" } });
        await settle();

        if (connection) connection.iceConnectionState = "failed";
        connection?.dispatch("iceconnectionstatechange");
        await settle();

        expect(connection?.offerOptions).toEqual([undefined, { iceRestart: true }]);
        expect(sentOfType(SIGNAL_TYPE.OFFER)).toHaveLength(2);
    });
});

describe("answering an offer", () => {
    const offer = { from: "p1", sdp: { type: "offer", sdp: "o" } };

    it("creates a connection that sends no offer and answers", async () => {
        const mesh = await freshMesh();

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();

        expect(mesh.peerCount()).toBe(1);
        expect(sentOfType(SIGNAL_TYPE.OFFER)).toEqual([]);
        expect(sentOfType(SIGNAL_TYPE.ANSWER)).toEqual([
            { type: SIGNAL_TYPE.ANSWER, target: "p1", sdp: connectionOf().localDescription },
        ]);
    });

    it("reuses the connection of a person already met", async () => {
        const mesh = await freshMesh();
        await meetPeer(mesh, "p1");

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();

        expect(FakeConnection.instances).toHaveLength(1);
        expect(sentOfType(SIGNAL_TYPE.ANSWER)).toHaveLength(1);
    });

    it("caps the upload after it sent the answer", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("video");

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();

        expect(connectionOf().transceivers[0]?.sender.capped).toEqual([2_000_000, 2_000_000]);
    });

    it("hands the local video over through the receive-only transceivers that can name a stream", async () => {
        const mesh = await freshMesh();
        const stream = streamWith("video");
        mesh.state.localStream = stream;
        await meetPeer(mesh, "p1");
        const connection = connectionOf();
        connection.transceivers = [
            { direction: "recvonly", receiver: { track: { kind: "video" } }, sender: new FakeSender(null) },
            { direction: "recvonly", receiver: { track: { kind: "video" } }, sender: new FakeSender(null) },
            { direction: "sendrecv", receiver: { track: { kind: "video" } }, sender: new FakeSender(null) },
        ];
        Object.defineProperty(connection.transceivers[1]?.sender, "setStreams", { value: undefined });

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();

        const [handedOver, withoutSetStreams, alreadySending] = connection.transceivers;
        expect(handedOver?.direction).toBe("sendrecv");
        expect(handedOver?.sender.setStreams).toHaveBeenCalledWith(stream);
        expect(handedOver?.sender.replaceTrack).toHaveBeenCalledWith(stream.getVideoTracks()[0]);
        expect(withoutSetStreams?.direction).toBe("recvonly");
        expect(alreadySending?.sender.replaceTrack).not.toHaveBeenCalled();
    });

    it("hands over no track when the local stream has no camera", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("audio");
        await meetPeer(mesh, "p1");
        const connection = connectionOf();
        connection.transceivers = [
            { direction: "recvonly", receiver: { track: { kind: "video" } }, sender: new FakeSender(null) },
        ];

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();

        expect(connection.transceivers[0]?.sender.replaceTrack).toHaveBeenCalledWith(null);
    });

    it("sends no answer when the connection closed while the offer was applied", async () => {
        await freshMesh();
        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();
        signal.sent.length = 0;
        connectionOf().closeAfterRemoteDescription = true;

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();

        expect(sentOfType(SIGNAL_TYPE.ANSWER)).toEqual([]);
        expect(reportError).not.toHaveBeenCalled();
    });

    it("sends no answer when the connection closed while the answer was made", async () => {
        await freshMesh();
        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();
        signal.sent.length = 0;
        connectionOf().closeAfterAnswer = true;

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();

        expect(sentOfType(SIGNAL_TYPE.ANSWER)).toEqual([]);
    });
});

describe("answers and candidates", () => {
    const answer = { from: "p1", sdp: { type: "answer", sdp: "a" } };

    it("applies an answer to a connection that waits for one", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");

        deliver(SIGNAL_TYPE.ANSWER, answer);
        await settle();

        expect(connection?.remoteDescriptions).toHaveLength(1);
    });

    it("ignores an answer once the connection is stable", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        deliver(SIGNAL_TYPE.ANSWER, answer);
        await settle();

        deliver(SIGNAL_TYPE.ANSWER, answer);
        await settle();

        expect(connection?.remoteDescriptions).toHaveLength(1);
    });

    it("ignores an answer from a person never met", async () => {
        const mesh = await freshMesh();

        deliver(SIGNAL_TYPE.ANSWER, answer);
        await settle();

        expect(mesh.peerCount()).toBe(0);
    });

    it("adds a candidate to the person's connection", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");

        deliver(SIGNAL_TYPE.ICE_CANDIDATE, { from: "p1", candidate: { candidate: "c" } });
        await settle();

        expect(connection?.candidates).toHaveLength(1);
    });

    it.each([
        ["without a candidate", { from: "p1" }],
        ["from a person never met", { from: "stranger", candidate: { candidate: "c" } }],
    ])("adds nothing for a candidate %s", async (_name, frame) => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");

        deliver(SIGNAL_TYPE.ICE_CANDIDATE, frame);
        await settle();

        expect(connection?.candidates).toEqual([]);
    });
});

describe("one person's signaling", () => {
    it("runs the steps in arrival order", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        const order: string[] = [];
        const original = connection?.setRemoteDescription.bind(connection) as FakeConnection["setRemoteDescription"];
        if (connection) {
            connection.setRemoteDescription = async (description) => {
                await Promise.resolve();
                order.push("answer");
                return original(description);
            };
            connection.addIceCandidate = (candidate) => {
                order.push("candidate");
                return Promise.resolve(candidate as undefined);
            };
        }

        deliver(SIGNAL_TYPE.ANSWER, { from: "p1", sdp: { type: "answer", sdp: "a" } });
        deliver(SIGNAL_TYPE.ICE_CANDIDATE, { from: "p1", candidate: { candidate: "c" } });
        await settle();

        expect(order).toEqual(["answer", "candidate"]);
    });

    it("drops a queued step when the person left before it ran", async () => {
        const mesh = await freshMesh();
        await meetPeer(mesh, "p1");

        deliver(SIGNAL_TYPE.ICE_CANDIDATE, { from: "p1", candidate: { candidate: "c" } });
        deliver(SIGNAL_TYPE.PARTICIPANT_LEFT, { participant_id: "p1" });
        deliver(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participants: ["p1"] });
        await settle();

        expect(FakeConnection.instances).toHaveLength(2);
        expect(connectionOf(1).candidates).toEqual([]);
    });

    it("reports a failed step and goes on with the next", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        const failure = new Error("bad candidate");
        const addIceCandidate = vi.fn().mockRejectedValueOnce(failure).mockResolvedValue(undefined);
        if (connection) {
            connection.addIceCandidate = addIceCandidate;
        }

        deliver(SIGNAL_TYPE.ICE_CANDIDATE, { from: "p1", candidate: { candidate: "c1" } });
        deliver(SIGNAL_TYPE.ICE_CANDIDATE, { from: "p1", candidate: { candidate: "c2" } });
        await settle();

        expect(reportError).toHaveBeenCalledExactlyOnceWith(failure);
        expect(addIceCandidate).toHaveBeenCalledTimes(2);
    });
});

describe("people leaving", () => {
    it("closes and forgets the connection and tells the page when the stage removed a tile", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        mesh.peersChanged.mockClear();

        deliver(SIGNAL_TYPE.PARTICIPANT_LEFT, { participant_id: "p1" });

        expect(connection?.signalingState).toBe("closed");
        expect(mesh.peerCount()).toBe(0);
        expect(mesh.stage.removeRemoteTile).toHaveBeenCalledWith("p1");
        expect(mesh.peersChanged).toHaveBeenCalledOnce();
    });

    it("stays silent when the stage had no tile", async () => {
        const mesh = await freshMesh();
        mesh.stage.removeRemoteTile.mockReturnValue(false);

        deliver(SIGNAL_TYPE.PARTICIPANT_LEFT, { participant_id: "nobody" });

        expect(mesh.peersChanged).not.toHaveBeenCalled();
    });

    it("lets people come and go before the page asks to hear of it", async () => {
        const mesh = await freshMesh({ watchPeers: false });
        await meetPeer(mesh, "p1");

        deliver(SIGNAL_TYPE.PARTICIPANT_LEFT, { participant_id: "p1" });

        expect(mesh.peerCount()).toBe(0);
    });

    it("removes every connection", async () => {
        const mesh = await freshMesh();
        const connections = await meetPeer(mesh, "p1", "p2", "p3");

        mesh.removeAllPeers();

        expect(mesh.peerCount()).toBe(0);
        expect(connections.map((connection) => connection.signalingState)).toEqual(["closed", "closed", "closed"]);
    });
});

describe("changing the local media on every connection", () => {
    it("adds a track to every connection", async () => {
        const mesh = await freshMesh();
        const connections = await meetPeer(mesh, "p1", "p2");

        mesh.addTrackToPeers(track("audio"), streamWith("audio"));

        expect(connections.map((connection) => connection.transceivers.length)).toEqual([1, 1]);
    });

    it("removes the track from the senders that carry it", async () => {
        const mesh = await freshMesh();
        const mine = track("audio");
        const [connection] = await meetPeer(mesh, "p1");
        connection?.addTrack(mine);
        connection?.addTrack(track("audio"));

        mesh.removeTrackFromPeers(mine);

        expect(connection?.transceivers.map((transceiver) => transceiver.sender.track === null)).toEqual([true, false]);
    });

    it("leaves a connection alone that does not carry the track", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        connection?.addTrack(track("audio"));

        mesh.removeTrackFromPeers(track("audio"));

        expect(connection?.transceivers[0]?.sender.track).not.toBeNull();
    });

    it("replaces the video track on every video transceiver and caps again", async () => {
        const mesh = await freshMesh();
        const connections = await meetPeer(mesh, "p1", "p2");
        for (const connection of connections) connection.addTrack(track("video"));
        const screen = track("video");

        await mesh.replaceVideoTrackOnPeers(screen);

        for (const connection of connections) {
            expect(connection.transceivers[0]?.sender.replaceTrack).toHaveBeenCalledWith(screen);
            expect(connection.transceivers[0]?.sender.capped).toEqual([1_000_000]);
        }
    });
});
