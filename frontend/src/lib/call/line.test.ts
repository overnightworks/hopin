import { describe, expect, it } from "vitest";
import { cleanLine, LINE_REFUSAL, LineBudget, lineRefusal } from "#lib/call/line.ts";
import { LINE_BURST, LINE_MAX_CHARACTERS } from "#lib/wire.ts";
import lineCases from "../../../../tests/config/fixtures/line_cases.json";

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

describe("why a text is no line", () => {
    it.each([
        ["too long", "x".repeat(LINE_MAX_CHARACTERS + 1), LINE_REFUSAL.TOO_LONG],
        ["holding a control character", "a\tb", LINE_REFUSAL.REFUSED_CHARACTER],
        ["a line", "hello", null],
        ["blank", "   ", null],
    ])("%s", (_name, text, expected) => {
        expect(lineRefusal(text)).toBe(expected);
    });
});

describe("a sender's budget", () => {
    function budgetAt(clock: { time: number }, capacity?: number) {
        return new LineBudget(() => clock.time, capacity);
    }

    it("holds the burst, refuses the next line and refills one line a second", () => {
        const clock = { time: 0 };
        const budget = budgetAt(clock);

        const burst = Array.from({ length: LINE_BURST }, () => budget.spend());
        const refused = budget.spend();
        clock.time += budget.waitMilliseconds();
        const refilled = budget.spend();

        expect([burst.every(Boolean), refused, refilled]).toEqual([true, false, true]);
        expect(budget.waitMilliseconds()).toBe(1000);
    });

    it("never holds more than its capacity however long it rests", () => {
        const clock = { time: 0 };
        const budget = budgetAt(clock, 2);

        clock.time += 3_600_000;

        expect([budget.spend(), budget.spend(), budget.spend()]).toEqual([true, true, false]);
    });
});
