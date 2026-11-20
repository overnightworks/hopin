import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { FakeConnection, FakeDescription, type Frame } from "./mesh.fixture.ts";
import type { StagePort } from "./stage-port.ts";

const signal = vi.hoisted(() => ({ handlers: new Map<string, ((frame: Frame) => void)[]>() }));

vi.mock("#lib/call/signaling.ts", () => ({
    on: (type: string, handler: (frame: Frame) => void) => {
        signal.handlers.set(type, [...(signal.handlers.get(type) ?? []), handler]);
    },
    send: () => {},
    isConnected: () => true,
    isLive: () => true,
}));

// The module holds its connections, so each case loads a fresh copy.
async function freshMesh() {
    vi.resetModules();
    signal.handlers.clear();
    FakeConnection.instances = [];
    const mesh = await import("./mesh.ts");
    const { installStage } = await import("./stage-port.ts");
    const stage = {
        addRemoteTile: vi.fn(),
        removeRemoteTile: vi.fn(() => true),
        showRemoteStream: vi.fn(),
        setConnectionQuality: vi.fn(),
        setRemoteSpeaking: vi.fn(),
    };
    installStage(stage as unknown as StagePort);
    mesh.registerMeshHandlers();
}

function deliver(type: SIGNAL_TYPE, frame: Frame = {}) {
    for (const handler of signal.handlers.get(type) ?? []) handler({ type, ...frame });
}

function connectionOf() {
    return FakeConnection.instances[0] as FakeConnection;
}

async function meetPeer(...participants: string[]) {
    deliver(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participants });
    await vi.advanceTimersByTimeAsync(0);
}

beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("RTCPeerConnection", FakeConnection);
    vi.stubGlobal("RTCSessionDescription", FakeDescription);
    vi.stubGlobal("RTCIceCandidate", FakeDescription);
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

describe("a file channel", () => {
    it("is opened by the page that fetches, in the person's connection, and a channel the person opens is served", async () => {
        await freshMesh();
        const { files } = await import("./files.ts");
        await meetPeer("p1");
        const connection = connectionOf();
        const served: unknown[] = [];
        const unknownFile = { label: "file:99", close: () => void served.push("closed") };

        files.announced("p1", 3, { name: "a.png", size: 10, mime: "image/png" });
        connection.ondatachannel?.({ channel: unknownFile });

        expect(connection.dataChannels.map((channel) => channel.label)).toEqual(["line", "file:3"]);
        expect(connection.dataChannels[1]?.options).toEqual({ ordered: true });
        expect(served).toEqual(["closed"]);
    });

    it("stops fetching with the person who leaves", async () => {
        await freshMesh();
        const { files } = await import("./files.ts");
        const shown: string[] = [];
        files.listen({ shown: () => {}, changed: (view) => void shown.push(view.state) });
        await meetPeer("p1");
        files.announced("p1", 3, { name: "a.png", size: 10, mime: "image/png" });

        deliver(SIGNAL_TYPE.PARTICIPANT_LEFT, { participant_id: "p1" });

        expect(shown).toEqual(["loading", "incomplete"]);
    });
});
