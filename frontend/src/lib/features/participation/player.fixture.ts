import { vi } from "vitest";
import type { Player, PlaybackEnd, PlaybackStart, Sound } from "overnightworks-speech-browser";

/** A sound the player was handed; the test starts it and ends it by hand. */
export interface FakeSound {
    readonly sound: Sound;
    start(): Promise<void>;
    end(end: PlaybackEnd): Promise<void>;
}

export interface FakePlayer {
    readonly sounds: FakeSound[];
    closed: boolean;
}

// The feature reads a playback one promise turn after it resolves.
async function settled() {
    await Promise.resolve();
    await Promise.resolve();
}

/** The players a feature created, in order, and the `createPlayer` that creates them. */
export function fakePlayers() {
    const created: FakePlayer[] = [];
    const createPlayer = (): Player => {
        const fake: FakePlayer = { sounds: [], closed: false };
        created.push(fake);
        return {
            play: (sound) => {
                const started = Promise.withResolvers<PlaybackStart>();
                const ended = Promise.withResolvers<PlaybackEnd>();
                fake.sounds.push({
                    sound,
                    start: async () => {
                        started.resolve({ kind: "playing", contextTime: 0, context: {} as AudioContext });
                        await settled();
                    },
                    end: async (end) => {
                        started.resolve({ kind: "ended" });
                        ended.resolve(end);
                        await settled();
                    },
                });
                return { started: started.promise, ended: ended.promise };
            },
            stop: vi.fn(),
            setMuted: vi.fn(),
            close: () => {
                fake.closed = true;
            },
        };
    };
    return { created, createPlayer };
}
