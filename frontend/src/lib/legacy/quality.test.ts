import { expect, it } from "vitest";
import { qualityOfRoundTrip } from "./quality.js";

const qualityByRoundTripSeconds: [number, string][] = [
    [0.05, "good"],
    [0.299, "good"],
    [0.3, "fair"],
    [0.499, "fair"],
    [0.5, "poor"],
    [2, "poor"],
];

it.each(qualityByRoundTripSeconds)("a round trip of %f s is %s", (roundTripSeconds, quality) => {
    expect(qualityOfRoundTrip(roundTripSeconds)).toBe(quality);
});
