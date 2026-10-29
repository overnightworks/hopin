import { describe, expect, it, vi } from "vitest";

// stage.js queries the page's markup when it loads; the decision under test never touches the stage.
vi.mock("./stage.js", () => ({ setRemoteSpeaking: vi.fn() }));

const { isSpeaking } = await import("./speaking.js");

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
