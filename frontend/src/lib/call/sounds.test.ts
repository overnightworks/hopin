import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface Tone {
    frequency: number;
    stoppedAt: number;
    fadedAt: number;
}

const CURRENT_TIME_SECONDS = 5;

function audioContextPlaying(tones: Tone[]) {
    return class {
        currentTime = CURRENT_TIME_SECONDS;
        destination = {};

        createOscillator() {
            const tone: Tone = { frequency: 0, stoppedAt: 0, fadedAt: 0 };
            tones.push(tone);
            return {
                type: "",
                frequency: {
                    set value(hertz: number) {
                        tone.frequency = hertz;
                    },
                },
                connect: vi.fn(),
                start: vi.fn(),
                stop: (at: number) => {
                    tone.stoppedAt = at;
                },
            };
        }

        createGain() {
            const tone = tones[tones.length - 1] as Tone;
            return {
                gain: {
                    value: 0,
                    exponentialRampToValueAtTime: (_floor: number, at: number) => {
                        tone.fadedAt = at;
                    },
                },
                connect: vi.fn(),
            };
        }
    };
}

async function soundsWith(constructorName: "AudioContext" | "webkitAudioContext", tones: Tone[]) {
    vi.stubGlobal(constructorName, audioContextPlaying(tones));
    vi.resetModules();
    return await import("./sounds.ts");
}

describe("sounds", () => {
    let tones: Tone[];

    beforeEach(() => {
        vi.useFakeTimers();
        tones = [];
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    it("plays the join sound as a low tone, then a high tone 100 ms later", async () => {
        const { playJoinSound } = await soundsWith("AudioContext", tones);

        playJoinSound();
        expect(tones.map((tone) => tone.frequency)).toEqual([660]);
        vi.advanceTimersByTime(99);
        expect(tones).toHaveLength(1);
        vi.advanceTimersByTime(1);

        expect(tones.map((tone) => tone.frequency)).toEqual([660, 880]);
        expect(tones.map((tone) => tone.stoppedAt)).toEqual([CURRENT_TIME_SECONDS + 0.15, CURRENT_TIME_SECONDS + 0.15]);
    });

    it("plays the leave sound as a high tone, then a longer low tone", async () => {
        const { playLeaveSound } = await soundsWith("AudioContext", tones);

        playLeaveSound();
        vi.advanceTimersByTime(100);

        expect(tones.map((tone) => tone.frequency)).toEqual([440, 330]);
        expect(tones.map((tone) => tone.stoppedAt)).toEqual([CURRENT_TIME_SECONDS + 0.15, CURRENT_TIME_SECONDS + 0.2]);
        expect(tones.map((tone) => tone.fadedAt)).toEqual(tones.map((tone) => tone.stoppedAt));
    });

    it("plays the message sound as one short tone", async () => {
        const { playMessageSound } = await soundsWith("AudioContext", tones);

        playMessageSound();
        vi.advanceTimersByTime(1000);

        expect(tones.map((tone) => tone.frequency)).toEqual([800]);
        expect(tones.map((tone) => tone.stoppedAt)).toEqual([CURRENT_TIME_SECONDS + 0.1]);
    });

    it("falls back to the prefixed audio context", async () => {
        const { playMessageSound } = await soundsWith("webkitAudioContext", tones);

        playMessageSound();

        expect(tones).toHaveLength(1);
    });

    it("creates its audio context when the module loads, before any sound plays", async () => {
        const created = vi.fn();
        vi.stubGlobal(
            "AudioContext",
            class {
                constructor() {
                    created();
                }
            },
        );
        vi.resetModules();

        await import("./sounds.ts");

        expect(created).toHaveBeenCalledTimes(1);
    });
});
