import { expect, it } from "vitest";
import { CONNECTION_QUALITY } from "#lib/call/stage-port.ts";
import { qualityOfRoundTrip } from "./quality.js";

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
