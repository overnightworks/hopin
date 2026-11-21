import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONNECTION_OUTCOME, ICE_SERVERS_PATH, SIGNAL_TYPE } from "#lib/wire.ts";
import { FakeConnection, FakeDescription, FakeSender, type Frame } from "./mesh.fixture.ts";
import type { StagePort } from "./stage-port.ts";

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
    isLive: () => true,
}));

function track(kind: string) {
    return { kind } as unknown as MediaStreamTrack;
}

let streamCount = 0;

function streamWith(...kinds: string[]) {
    const tracks = kinds.map(track);
    const listeners: (() => void)[] = [];
    streamCount += 1;
    return {
        id: `stream-${String(streamCount)}`,
        getTracks: () => tracks,
        getVideoTracks: () => tracks.filter((candidate) => candidate.kind === "video"),
        addEventListener: (_type: string, listener: () => void) => listeners.push(listener),
        loseTracks: () => {
            tracks.length = 0;
            for (const listener of listeners) listener();
        },
    } as unknown as MediaStream & { loseTracks(): void };
}

const reportError = vi.fn();
const iceServers = [{ urls: "ice-server-from-the-endpoint" }];

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
    FakeConnection.reset();
    const mesh = await import("./mesh.ts");
    const { installStage } = await import("./stage-port.ts");
    const { state } = await import("./state.ts");
    const stage = {
        addRemoteTile: vi.fn(),
        removeRemoteTile: vi.fn(() => true),
        showRemoteStream: vi.fn(),
        showRemoteScreen: vi.fn(),
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

    it("carries the status of any other failure", async () => {
        const mesh = await freshMesh();
        respondToFetch(503);

        await expect(mesh.loadIceServers()).rejects.toThrow(mesh.IceServersUnavailableError);
        await expect(mesh.loadIceServers()).rejects.toMatchObject({ status: 503 });
    });

    it("connects with no servers until they are loaded", async () => {
        const mesh = await freshMesh();

        await meetPeer(mesh, "p1");

        expect(connectionOf().configuration).toEqual({ iceServers: [] });
    });
});

describe("the line channel", () => {
    it("is negotiated with the same id on both sides and made before the first offer is made", async () => {
        const mesh = await freshMesh();

        await meetPeer(mesh, "p1");

        expect(connectionOf().steps.slice(0, 2)).toEqual(["data channel", "offer"]);
        expect(connectionOf().dataChannels).toMatchObject([
            { label: "line", options: { negotiated: true, id: 0, ordered: true } },
        ]);
    });

    it("is made before an offer is applied, for a person who calls", async () => {
        const mesh = await freshMesh();

        deliver(SIGNAL_TYPE.OFFER, { from: "p9", sdp: { type: "offer", sdp: "theirs" } });
        await settle();

        expect(connectionOf().steps.slice(0, 2)).toEqual(["data channel", "remote description"]);
        expect(mesh.peerCount()).toBe(1);
    });

    it("carries a line to the person once it opens and drops with the person", async () => {
        const mesh = await freshMesh();
        const { lines } = await import("./lines.ts");
        await meetPeer(mesh, "p1");
        const channel = connectionOf().dataChannels[0];
        const sentLines: unknown[] = [];
        lines.listen({
            received: () => {},
            announced: () => {},
            delivery: (id, delivery) => void sentLines.push([id, delivery]),
        });

        lines.send("hello");
        expect(channel?.sent).toEqual([]);
        (channel as { readyState: string }).readyState = "open";
        (channel as unknown as { onopen: () => void }).onopen();
        deliver(SIGNAL_TYPE.PARTICIPANT_LEFT, { participant_id: "p1" });

        expect(channel?.sent).toEqual([JSON.stringify({ type: "line", id: 1, text: "hello" })]);
        expect(sentLines).toEqual([[1, "undelivered"]]);
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
        const releaseOffer = FakeConnection.holdOffers();
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

    it("reports to the server how a peer connection came up", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");

        connection?.dispatch("connectionstatechange");
        await settle();

        expect(sentOfType(SIGNAL_TYPE.CONNECTION_OUTCOME)).toEqual([
            { type: SIGNAL_TYPE.CONNECTION_OUTCOME, outcome: CONNECTION_OUTCOME.DIRECT },
        ]);
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
        deliver(SIGNAL_TYPE.ANSWER, { from: "p1", sdp: { type: "answer", sdp: "a" } });
        await settle();

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
        FakeConnection.incoming.push(
            { direction: "recvonly", receiver: { track: { kind: "video" } }, sender: new FakeSender(null) },
            { direction: "recvonly", receiver: { track: { kind: "video" } }, sender: new FakeSender(null) },
            { direction: "sendrecv", receiver: { track: { kind: "video" } }, sender: new FakeSender(null) },
        );
        Object.defineProperty(FakeConnection.incoming[1]?.sender, "setStreams", { value: undefined });

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();
        const connection = connectionOf();

        const [handedOver, withoutSetStreams, alreadySending] = connection.transceivers.slice(1);
        expect(handedOver?.direction).toBe("sendrecv");
        expect(handedOver?.sender.setStreams).toHaveBeenCalledWith(stream);
        expect(handedOver?.sender.replaceTrack).toHaveBeenCalledWith(stream.getVideoTracks()[0]);
        expect(withoutSetStreams?.direction).toBe("recvonly");
        expect(alreadySending?.sender.replaceTrack).not.toHaveBeenCalled();
    });

    it("hands over no track when the local stream has no camera", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("audio");
        FakeConnection.incoming.push({
            direction: "recvonly",
            receiver: { track: { kind: "video" } },
            sender: new FakeSender(null),
        });

        deliver(SIGNAL_TYPE.OFFER, offer);
        await settle();
        const connection = connectionOf();

        expect(connection.transceivers.at(-1)?.sender.replaceTrack).toHaveBeenCalledWith(null);
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
    it("sends the screen on a transceiver of its own beside the camera of every connection", async () => {
        const mesh = await freshMesh();
        const camera = streamWith("audio", "video");
        mesh.state.localStream = camera;
        const connections = await meetPeer(mesh, "p1", "p2");
        const screen = streamWith("video", "audio");

        mesh.addScreenToPeers(screen);

        for (const connection of connections) {
            expect(connection.transceivers.map((transceiver) => transceiver.direction)).toEqual([
                "sendrecv",
                "sendrecv",
                "sendonly",
                "sendonly",
            ]);
            expect(connection.transceivers.slice(2).map((transceiver) => transceiver.sender.track)).toEqual(
                screen.getTracks(),
            );
        }
    });

    it("takes the screen's tracks from the senders that carry them and keeps the camera's", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("audio", "video");
        const [connection] = await meetPeer(mesh, "p1");
        const screen = streamWith("video", "audio");
        mesh.addScreenToPeers(screen);

        mesh.removeScreenFromPeers(screen);

        expect(connection?.transceivers.map((transceiver) => transceiver.sender.track === null)).toEqual([
            false,
            false,
            true,
            true,
        ]);
    });

    it("leaves a connection alone that does not carry the screen", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        connection?.addTrack(track("audio"));

        mesh.removeScreenFromPeers(streamWith("audio"));

        expect(connection?.transceivers[0]?.sender.track).not.toBeNull();
    });

    it("replaces the video track on every camera transceiver and caps again, never on the screen's", async () => {
        const mesh = await freshMesh();
        const connections = await meetPeer(mesh, "p1", "p2");
        for (const connection of connections) connection.addTrack(track("video"));
        mesh.addScreenToPeers(streamWith("video"));
        const camera = track("video");

        await mesh.replaceVideoTrackOnPeers(camera);

        for (const connection of connections) {
            expect(connection.transceivers[0]?.sender.replaceTrack).toHaveBeenCalledWith(camera);
            expect(connection.transceivers[1]?.sender.replaceTrack).not.toHaveBeenCalled();
            expect(connection.transceivers[0]?.sender.capped).toEqual([1_000_000]);
        }
    });
});

describe("a screen beside the camera", () => {
    it("offers again when a screen is added after the first exchange", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        signal.sent.length = 0;

        connection?.onnegotiationneeded?.();
        await settle();

        expect(sentOfType(SIGNAL_TYPE.OFFER)).toHaveLength(1);
    });

    it("does not offer on a change that the first exchange carries", async () => {
        const mesh = await freshMesh();
        const gate = FakeConnection.holdOffers();
        deliver(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participants: ["p1"] });
        await settle();
        signal.sent.length = 0;

        connectionOf().onnegotiationneeded?.();
        gate();
        await settle();

        expect(sentOfType(SIGNAL_TYPE.OFFER)).toHaveLength(1);
        expect(mesh.peerCount()).toBe(1);
    });

    it("sends a screen being shared to a person who joins during the share", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("audio", "video");
        mesh.state.screenStream = streamWith("video");

        await meetPeer(mesh, "p1");

        expect(connectionOf().transceivers.map((transceiver) => transceiver.direction)).toEqual([
            "sendrecv",
            "sendrecv",
            "sendonly",
        ]);
    });

    it("shows the first stream of a person as their camera and a later one as their screen", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        const camera = streamWith("audio", "video");
        const screen = streamWith("video");

        connection?.ontrack?.({ streams: [camera], transceiver: {} });
        connection?.ontrack?.({ streams: [screen], transceiver: {} });

        expect(mesh.stage.showRemoteStream.mock.calls).toEqual([["p1", camera]]);
        expect(mesh.stage.showRemoteScreen.mock.calls).toEqual([["p1", screen]]);
    });

    it("ends the shown screen when its stream loses its tracks", async () => {
        const mesh = await freshMesh();
        const [connection] = await meetPeer(mesh, "p1");
        const screen = streamWith("video");
        connection?.ontrack?.({ streams: [streamWith("audio")], transceiver: {} });
        connection?.ontrack?.({ streams: [screen], transceiver: {} });

        screen.loseTracks();

        expect(mesh.stage.showRemoteScreen).toHaveBeenLastCalledWith("p1", null);
    });

    it("does not hand the camera over on a receive-only video that a later offer brings", async () => {
        const mesh = await freshMesh();
        mesh.state.localStream = streamWith("video");
        await meetPeer(mesh, "p1");
        const connection = connectionOf();
        deliver(SIGNAL_TYPE.ANSWER, { from: "p1", sdp: { type: "answer", sdp: "a" } });
        await settle();
        const incomingScreen = {
            direction: "recvonly",
            receiver: { track: { kind: "video" } },
            sender: new FakeSender(null),
        };
        connection.transceivers.push(incomingScreen);

        deliver(SIGNAL_TYPE.OFFER, { from: "p1", sdp: { type: "offer", sdp: "screen" } });
        await settle();

        expect(incomingScreen.direction).toBe("recvonly");
        expect(incomingScreen.sender.replaceTrack).not.toHaveBeenCalled();
    });

    it("ignores an offer that meets its own offer when it is the impolite side", async () => {
        const mesh = await freshMesh();
        await meetPeer(mesh, "p1");
        signal.sent.length = 0;

        deliver(SIGNAL_TYPE.OFFER, { from: "p1", sdp: { type: "offer", sdp: "o" } });
        await settle();

        expect(sentOfType(SIGNAL_TYPE.ANSWER)).toEqual([]);
        expect(connectionOf().remoteDescriptions).toEqual([]);
    });

    it("gives way to an offer that meets its own offer when it is the polite side", async () => {
        await freshMesh();
        deliver(SIGNAL_TYPE.OFFER, { from: "p1", sdp: { type: "offer", sdp: "first" } });
        await settle();
        const connection = connectionOf();
        await connection.setLocalDescription({ type: "offer" });
        signal.sent.length = 0;

        deliver(SIGNAL_TYPE.OFFER, { from: "p1", sdp: { type: "offer", sdp: "second" } });
        await settle();

        expect(sentOfType(SIGNAL_TYPE.ANSWER)).toHaveLength(1);
    });

    it("lets a candidate of an ignored offer fail without a report", async () => {
        const mesh = await freshMesh();
        await meetPeer(mesh, "p1");
        deliver(SIGNAL_TYPE.OFFER, { from: "p1", sdp: { type: "offer", sdp: "o" } });
        await settle();
        connectionOf().addIceCandidate = () => Promise.reject(new Error("no remote description"));

        deliver(SIGNAL_TYPE.ICE_CANDIDATE, { from: "p1", candidate: { candidate: "c" } });
        await settle();

        expect(reportError).not.toHaveBeenCalled();
        expect(mesh.peerCount()).toBe(1);
    });
});
