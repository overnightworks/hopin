import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONNECTION_QUALITY, installStage, type StagePort } from "./stage-port.ts";
import { monitorConnectionQuality, qualityOfRoundTrip } from "./quality.ts";

const qualityReports: [string, string][] = [];
installStage({
    setConnectionQuality: (participantId, quality) => {
        qualityReports.push([participantId, quality]);
    },
} as StagePort);

const qualityByRoundTripSeconds: [number, string][] = [
    [0.05, CONNECTION_QUALITY.GOOD],
    [0.299, CONNECTION_QUALITY.GOOD],
    [0.3, CONNECTION_QUALITY.FAIR],
    [0.499, CONNECTION_QUALITY.FAIR],
    [0.5, CONNECTION_QUALITY.POOR],
    [2, CONNECTION_QUALITY.POOR],
];

it.each(qualityByRoundTripSeconds)("a round trip of %f s is %s", (roundTripSeconds, quality) => {
    expect(qualityOfRoundTrip(roundTripSeconds)).toBe(quality);
});

type FakeReport = Record<string, unknown>;

function connectionReporting(reports: FakeReport[]) {
    return {
        connectionState: "connected" as RTCPeerConnectionState,
        getStats: () => Promise.resolve(new Map(reports.map((report, index) => [String(index), report]))),
    };
}

function pair(currentRoundTripTime: number | undefined, state = "succeeded"): FakeReport {
    return { type: "candidate-pair", state, currentRoundTripTime };
}

async function pollOnce(): Promise<void> {
    await vi.advanceTimersByTimeAsync(3000);
}

describe("monitorConnectionQuality", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        qualityReports.length = 0;
    });
    afterEach(() => {
        vi.useRealTimers();
    });

    it.each([
        [0.1, CONNECTION_QUALITY.GOOD],
        [0.4, CONNECTION_QUALITY.FAIR],
        [0.9, CONNECTION_QUALITY.POOR],
    ])("a succeeded pair with a round trip of %f s reports %s", async (roundTrip, quality) => {
        monitorConnectionQuality("p1", connectionReporting([pair(roundTrip)]));

        await pollOnce();

        expect(qualityReports).toEqual([["p1", quality]]);
    });

    it.each([
        ["a report that is not a pair", { type: "inbound-rtp", currentRoundTripTime: 0.1 }],
        ["a pair not yet succeeded", pair(0.1, "in-progress")],
        ["a pair without a round-trip time", pair(undefined)],
    ])("%s reports nothing", async (_name, report) => {
        monitorConnectionQuality("p1", connectionReporting([report]));

        await pollOnce();

        expect(qualityReports).toEqual([]);
    });

    it("applies every succeeded pair in report order, so the last one wins", async () => {
        monitorConnectionQuality("p1", connectionReporting([pair(0.1), pair(0.9)]));

        await pollOnce();

        expect(qualityReports).toEqual([
            ["p1", CONNECTION_QUALITY.GOOD],
            ["p1", CONNECTION_QUALITY.POOR],
        ]);
    });

    it("reports nothing for empty stats", async () => {
        monitorConnectionQuality("p1", connectionReporting([]));

        await pollOnce();

        expect(qualityReports).toEqual([]);
    });

    it("stops polling once the connection is closed", async () => {
        const connection = connectionReporting([pair(0.1)]);
        monitorConnectionQuality("p1", connection);
        connection.connectionState = "closed";

        await pollOnce();

        expect(qualityReports).toEqual([]);
        expect(vi.getTimerCount()).toBe(0);
    });

    it("keeps polling while the connection has failed", async () => {
        const connection = connectionReporting([pair(0.1)]);
        monitorConnectionQuality("p1", connection);
        connection.connectionState = "failed";

        await pollOnce();
        await pollOnce();

        expect(qualityReports).toHaveLength(2);
    });
});
