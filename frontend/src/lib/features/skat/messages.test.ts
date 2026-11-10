import { describe, expect, it } from "vitest";
import { type CurrentTable, greetingFor } from "./messages.ts";

const FRAME_ORIGIN = "https://skat.example";
const TABLE = "0123456789abcdef0123456789abcdef";
const CURRENT: CurrentTable = { attachment: 7, table: TABLE };
const READY = { v: 1, type: "ready", table: TABLE };

const FRAME_WINDOW = {};

function received(overrides: { origin?: string; source?: unknown; frameWindow?: unknown; data?: unknown } = {}) {
    return { origin: FRAME_ORIGIN, source: FRAME_WINDOW, frameWindow: FRAME_WINDOW, data: READY, ...overrides };
}

describe("a message from the frame", () => {
    it("is answered with a hello for the attachment, aimed at the frame's exact origin", () => {
        expect(greetingFor(received(), FRAME_ORIGIN, CURRENT)).toEqual({
            hello: { v: 1, type: "hello", attachment: 7, attempt: null },
            targetOrigin: FRAME_ORIGIN,
        });
    });

    it.each<[string, Parameters<typeof received>[0]]>([
        ["another origin", { origin: "https://other.example" }],
        ["another window", { source: {} }],
        ["no sender window while the frame is detached", { source: null, frameWindow: null }],
        ["a version other than 1", { data: { ...READY, v: 2 } }],
        ["a version that is the text 1", { data: { ...READY, v: "1" } }],
        ["another type", { data: { ...READY, type: "hello" } }],
        ["another table", { data: { ...READY, table: "f".repeat(32) } }],
        ["a table that is no text", { data: { ...READY, table: 5 } }],
        ["a missing key", { data: { v: 1, type: "ready" } }],
        ["an extra key", { data: { ...READY, extra: "x" } }],
        ["a key named otherwise", { data: { v: 1, type: "ready", tabel: TABLE } }],
        ["a text", { data: JSON.stringify(READY) }],
        ["null", { data: null }],
        ["an array", { data: [1, "ready", TABLE] }],
        ["an object with another prototype", { data: Object.assign(Object.create({ inherited: 1 }), READY) }],
        [
            "a class instance",
            {
                data: new (class Ready {
                    v = 1;
                    type = "ready";
                    table = TABLE;
                })(),
            },
        ],
        ["an oversized table", { data: { ...READY, table: TABLE.repeat(4096) } }],
    ])("is ignored for %s", (_name, overrides) => {
        expect(greetingFor(received(overrides), FRAME_ORIGIN, CURRENT)).toBeNull();
    });

    it("is ignored when the attachment changed or none is shown", () => {
        const moved: CurrentTable = { attachment: 8, table: "f".repeat(32) };
        expect(greetingFor(received(), FRAME_ORIGIN, moved)).toBeNull();
        expect(greetingFor(received(), FRAME_ORIGIN, null)).toBeNull();
    });
});
