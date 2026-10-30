import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { CONNECTION_QUALITY, type StagePort } from "./stage-port.ts";

type StagePortModule = typeof import("./stage-port.ts");

const stream = {} as MediaStream;
const mediaState = { audio: true, video: false };

interface Operation {
    name: keyof StagePort;
    call: (port: StagePortModule) => unknown;
    expectedArguments: unknown[];
}

// One entry per operation, with the arguments the call passes.
const operations: Operation[] = [
    {
        name: "showLocalStream",
        call: (port) => {
            port.showLocalStream(stream);
        },
        expectedArguments: [stream],
    },
    {
        name: "updateLocalPreview",
        call: (port) => {
            port.updateLocalPreview();
        },
        expectedArguments: [],
    },
    {
        name: "addRemoteTile",
        call: (port) => {
            port.addRemoteTile("p1");
        },
        expectedArguments: ["p1"],
    },
    { name: "removeRemoteTile", call: (port) => port.removeRemoteTile("p1"), expectedArguments: ["p1"] },
    {
        name: "showRemoteStream",
        call: (port) => {
            port.showRemoteStream("p1", stream);
        },
        expectedArguments: ["p1", stream],
    },
    {
        name: "showRemoteName",
        call: (port) => {
            port.showRemoteName("p1");
        },
        expectedArguments: ["p1"],
    },
    {
        name: "setRemoteMediaState",
        call: (port) => {
            port.setRemoteMediaState("p1", mediaState);
        },
        expectedArguments: ["p1", mediaState],
    },
    {
        name: "setRemoteSpeaking",
        call: (port) => {
            port.setRemoteSpeaking("p1", true);
        },
        expectedArguments: ["p1", true],
    },
    { name: "hasTile", call: (port) => port.hasTile("p1"), expectedArguments: ["p1"] },
    {
        name: "setConnectionQuality",
        call: (port) => {
            port.setConnectionQuality("p1", CONNECTION_QUALITY.GOOD);
        },
        expectedArguments: ["p1", CONNECTION_QUALITY.GOOD],
    },
];

function fakeStage(): Record<keyof StagePort, Mock> {
    const fake = {} as Record<keyof StagePort, Mock>;
    for (const { name } of operations) fake[name] = vi.fn();
    return fake;
}

function install(stage: Record<keyof StagePort, Mock>): void {
    port.installStage(stage);
}

let port: StagePortModule;

beforeEach(async () => {
    vi.resetModules();
    port = await import("./stage-port.ts");
});

describe("stage port", () => {
    it.each(operations)("$name before install throws that the stage is not installed", ({ call }) => {
        expect(() => call(port)).toThrow(/stage is not installed/);
    });

    it.each(operations)(
        "$name hands its arguments to the installed stage",
        ({ name, call, expectedArguments }) => {
            const stage = fakeStage();
            install(stage);

            call(port);

            expect(stage[name]).toHaveBeenCalledWith(...expectedArguments);
        },
    );

    it.each([true, false])("removeRemoteTile and hasTile return the stage's answer %s", (answer) => {
        const stage = fakeStage();
        stage.removeRemoteTile.mockReturnValue(answer);
        stage.hasTile.mockReturnValue(answer);
        install(stage);

        expect(port.removeRemoteTile("p1")).toBe(answer);
        expect(port.hasTile("p1")).toBe(answer);
    });

    it("a second install throws and the first stage stays in place", () => {
        const first = fakeStage();
        const second = fakeStage();
        install(first);

        expect(() => {
            install(second);
        }).toThrow(/already installed/);

        port.updateLocalPreview();
        expect(first.updateLocalPreview).toHaveBeenCalledOnce();
        expect(second.updateLocalPreview).not.toHaveBeenCalled();
    });
});
