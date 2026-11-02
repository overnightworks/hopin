import { describe, expect, it, vi } from "vitest";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import type { StagePort } from "./stage-port.ts";

type Frame = Record<string, unknown>;

const signal = vi.hoisted(() => ({
    handlers: new Map<string, ((frame: Frame) => void)[]>(),
    sent: [] as Frame[],
    connected: true,
}));

vi.mock("#lib/call/signaling.ts", () => ({
    on: (type: string, handler: (frame: Frame) => void) => {
        signal.handlers.set(type, [...(signal.handlers.get(type) ?? []), handler]);
    },
    send: (message: Frame) => {
        signal.sent.push(message);
    },
    isConnected: () => signal.connected,
}));

// The module holds nothing, but the stage port and the call state do, so each case loads fresh copies.
async function freshMediaState() {
    vi.resetModules();
    signal.handlers.clear();
    signal.sent.length = 0;
    signal.connected = true;
    const mediaState = await import("./media-state.ts");
    const { installStage } = await import("./stage-port.ts");
    const { state } = await import("./state.ts");
    const stage = { setRemoteMediaState: vi.fn() };
    installStage(stage as unknown as StagePort);
    mediaState.registerMediaStateHandlers();
    return { ...mediaState, state, stage };
}

function deliver(type: SIGNAL_TYPE, frame: Frame = {}) {
    for (const handler of signal.handlers.get(type) ?? []) handler({ type, ...frame });
}

describe("announcing the own media state", () => {
    it("sends nothing while signaling is not connected", async () => {
        const { announceMediaState } = await freshMediaState();
        signal.connected = false;

        announceMediaState();

        expect(signal.sent).toEqual([]);
    });

    it.each([
        ["camera on", { videoEnabled: true, shares: false }, true],
        ["camera off", { videoEnabled: false, shares: false }, false],
        ["camera off and sharing the screen", { videoEnabled: false, shares: true }, true],
    ])("sends video for %s", async (_name, { videoEnabled, shares }, video) => {
        const { announceMediaState, state } = await freshMediaState();
        state.videoEnabled = videoEnabled;
        state.screenStream = shares ? ({} as MediaStream) : null;
        state.audioEnabled = false;

        announceMediaState();

        expect(signal.sent).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: false, video }]);
    });

    it.each([SIGNAL_TYPE.EXISTING_PARTICIPANTS, SIGNAL_TYPE.PARTICIPANT_JOINED])("announces on %s", async (type) => {
        await freshMediaState();

        deliver(type);

        expect(signal.sent).toEqual([{ type: SIGNAL_TYPE.MEDIA_STATE, audio: true, video: true }]);
    });
});

describe("receiving a media state", () => {
    it("hands the audio and video of the sender to the stage", async () => {
        const { stage } = await freshMediaState();

        deliver(SIGNAL_TYPE.MEDIA_STATE, { from: "p1", audio: false, video: true });

        expect(stage.setRemoteMediaState).toHaveBeenCalledWith("p1", { audio: false, video: true });
    });
});
