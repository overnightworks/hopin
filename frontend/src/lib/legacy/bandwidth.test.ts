import { expect, it } from "vitest";
import { videoBitrateCap } from "./bandwidth.js";

const videoCapByRemoteCount: [number, number][] = [
    [1, 2_000_000],
    [2, 1_000_000],
    [3, 666_666],
    [4, 500_000],
    [5, 400_000],
];

it.each(videoCapByRemoteCount)("video bitrate cap with %i remote peers is %i bps", (remoteCount, cap) => {
    expect(videoBitrateCap(remoteCount)).toBe(cap);
});
