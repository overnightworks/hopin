import { describe, expect, it } from "vitest";
import { cleanLine } from "#lib/call/line.ts";
import lineCases from "../../../../tests/config/line_cases.json";

interface LineCase {
    name: string;
    text: string;
    expected: string | null;
    repeat?: number;
}

const CASES: LineCase[] = lineCases;

describe("a text's judgement as a line", () => {
    it.each(CASES.map((lineCase) => [lineCase.name, lineCase] as const))("%s", (_name, lineCase) => {
        const repeat = lineCase.repeat ?? 1;
        const expected = lineCase.expected === null ? null : lineCase.expected.repeat(repeat);

        expect(cleanLine(lineCase.text.repeat(repeat))).toBe(expected);
    });
});
