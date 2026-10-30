import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installStage, type StagePort } from "./stage-port.ts";
import { isSpeaking, monitorSpeaking } from "./speaking.ts";

const speakingReports: [string, boolean][] = [];
installStage({
    setRemoteSpeaking: (participantId, speaking) => {
        speakingReports.push([participantId, speaking]);
    },
} as StagePort);

const NOW = 1_000_000;
const QUIET_FOR_LONG = 10_000;

interface Source {
    audioLevel: number;
    age: number;
}

const decisionByCase: Record<string, [Source[], number, boolean]> = {
    loud_and_fresh: [[{ audioLevel: 0.708, age: 20 }], QUIET_FOR_LONG, true],
    loud_but_stale: [[{ audioLevel: 0.708, age: 400 }], QUIET_FOR_LONG, false],
    hum_under_the_floor: [[{ audioLevel: 0.005, age: 20 }], QUIET_FOR_LONG, false],
    no_source: [[], QUIET_FOR_LONG, false],
    exactly_at_the_floor: [[{ audioLevel: 0.01, age: 20 }], QUIET_FOR_LONG, true],
    loudest_of_two_sources: [
        [
            { audioLevel: 0, age: 20 },
            { audioLevel: 0.5, age: 20 },
        ],
        QUIET_FOR_LONG,
        true,
    ],
    silent_within_the_hold: [[{ audioLevel: 0, age: 20 }], 799, true],
    silent_after_the_hold: [[{ audioLevel: 0, age: 20 }], 801, false],
};

describe("isSpeaking", () => {
    it.each(Object.entries(decisionByCase))(
        "follows the level, its freshness and the hold: %s",
        (_name, [sources, millisecondsSinceLoud, speaks]) => {
            const arrived = sources.map(({ audioLevel, age }) => ({ audioLevel, timestamp: NOW - age }));

            expect(isSpeaking(arrived, NOW, NOW - millisecondsSinceLoud)).toBe(speaks);
        },
    );
});

interface FakeReceiver {
    kind: string;
    sources: Source[];
}

function connectionWith(receivers: FakeReceiver[]) {
    const connection = {
        connectionState: "connected" as RTCPeerConnectionState,
        getReceivers: () =>
            receivers.map(({ kind, sources }) => ({
                track: { kind },
                getSynchronizationSources: () =>
                    sources.map(({ audioLevel, age }) => ({
                        audioLevel,
                        timestamp: performance.timeOrigin + performance.now() - age,
                    })),
            })),
    };
    return connection;
}

const loudAudio: FakeReceiver = { kind: "audio", sources: [{ audioLevel: 0.7, age: 20 }] };
const quietAudio: FakeReceiver = { kind: "audio", sources: [{ audioLevel: 0, age: 20 }] };

describe("monitorSpeaking", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        speakingReports.length = 0;
    });
    afterEach(() => {
        vi.useRealTimers();
    });

    it("reports nothing while the peer stays quiet", () => {
        monitorSpeaking("p1", connectionWith([quietAudio]));

        vi.advanceTimersByTime(1000);

        expect(speakingReports).toEqual([]);
    });

    it("reports only when the peer starts speaking, not on every poll", () => {
        monitorSpeaking("p1", connectionWith([loudAudio]));

        vi.advanceTimersByTime(500);

        expect(speakingReports).toEqual([["p1", true]]);
    });

    it("reports silence only after the hold has passed", () => {
        const receivers = [{ ...loudAudio }];
        monitorSpeaking("p1", connectionWith(receivers));
        vi.advanceTimersByTime(100);
        receivers[0] = quietAudio;

        vi.advanceTimersByTime(699);
        expect(speakingReports).toEqual([["p1", true]]);

        vi.advanceTimersByTime(101);
        expect(speakingReports).toEqual([
            ["p1", true],
            ["p1", false],
        ]);
    });

    it("ignores a stale source", () => {
        monitorSpeaking("p1", connectionWith([{ kind: "audio", sources: [{ audioLevel: 0.7, age: 400 }] }]));

        vi.advanceTimersByTime(500);

        expect(speakingReports).toEqual([]);
    });

    it("ignores video receivers", () => {
        monitorSpeaking("p1", connectionWith([{ ...loudAudio, kind: "video" }]));

        vi.advanceTimersByTime(500);

        expect(speakingReports).toEqual([]);
    });

    it("stops polling once the connection is closed", () => {
        const connection = connectionWith([loudAudio]);
        monitorSpeaking("p1", connection);
        connection.connectionState = "closed";

        vi.advanceTimersByTime(1000);

        expect(speakingReports).toEqual([]);
        expect(vi.getTimerCount()).toBe(0);
    });
});
