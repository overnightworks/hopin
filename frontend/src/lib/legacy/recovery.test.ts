import { expect, it } from "vitest";
import { restartDelay } from "./recovery.js";

const restartDelayByAttempt: [number, number][] = [
    [0, 2000],
    [1, 4000],
    [2, 8000],
    [3, 15000],
    [4, 15000],
    [5, 15000],
];

it.each(restartDelayByAttempt)("restart delay of attempt %i is %i ms and the last delay repeats", (attempt, delay) => {
    expect(restartDelay(attempt)).toBe(delay);
});
