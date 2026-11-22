// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

// The modules hold their state, so each case loads fresh copies.
async function freshStage() {
    vi.resetModules();
    return await import("#lib/ui/stage.svelte.ts");
}

describe("an agent tile's compact chips", () => {
    it.each([
        {
            figured: false,
            narrow: false,
            spacious: true,
            seeing: false,
            expected: { hearing: false, seeing: false, listening: false },
        },
        {
            figured: false,
            narrow: false,
            spacious: false,
            seeing: false,
            expected: { hearing: false, seeing: false, listening: true },
        },
        {
            figured: false,
            narrow: false,
            spacious: true,
            seeing: true,
            expected: { hearing: false, seeing: false, listening: true },
        },
        {
            figured: true,
            narrow: false,
            spacious: true,
            seeing: false,
            expected: { hearing: true, seeing: true, listening: true },
        },
        {
            figured: false,
            narrow: true,
            spacious: true,
            seeing: false,
            expected: { hearing: false, seeing: true, listening: true },
        },
        {
            figured: true,
            narrow: true,
            spacious: true,
            seeing: false,
            expected: { hearing: false, seeing: true, listening: true },
        },
        {
            figured: false,
            narrow: true,
            spacious: true,
            seeing: true,
            expected: { hearing: true, seeing: true, listening: true },
        },
        {
            figured: true,
            narrow: true,
            spacious: true,
            seeing: true,
            expected: { hearing: true, seeing: true, listening: true },
        },
    ])(
        "CALL-77: shrinks $expected for figured $figured, narrow $narrow, spacious $spacious and seeing $seeing",
        async ({ figured, narrow, spacious, seeing, expected }) => {
            const stage = await freshStage();

            expect(stage.compactChips({ figured, narrow, spacious, seeing })).toEqual(expected);
        },
    );
});
