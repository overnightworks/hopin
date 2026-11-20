// The peer connection a mesh case runs against: it records what the mesh asks of it and lets a case drive what it reports back.
import { vi } from "vitest";

export type Frame = Record<string, unknown>;

export class FakeSender {
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

export interface FakeTransceiver {
    direction: string;
    receiver: { track: { kind: string } };
    sender: FakeSender;
}

export class FakeConnection {
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
    // What the connection was asked to do before its first offer, in order, and every data channel it made.
    steps: string[] = [];
    dataChannels: { label: string; options: unknown; sent: string[]; readyState: string }[] = [];
    ondatachannel: ((event: { channel: unknown }) => void) | null = null;
    private readonly listeners = new Map<string, (() => void)[]>();

    constructor(readonly configuration: unknown) {
        FakeConnection.instances.push(this);
    }

    createDataChannel(label: string, options: unknown) {
        this.steps.push("data channel");
        const channel = {
            label,
            options,
            sent: [] as string[],
            readyState: "connecting",
            send: (data: string) => channel.sent.push(data),
            close: () => {
                channel.readyState = "closed";
            },
        };
        this.dataChannels.push(channel);
        return channel;
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
        this.steps.push("offer");
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
        this.steps.push("remote description");
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

export class FakeDescription {
    constructor(readonly init: Frame) {}
}
