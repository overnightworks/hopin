import type { Player, PlaybackEnd, PlaybackStart, Sound } from "overnightworks-speech-browser";

/** A sound the player was handed; the test starts it and ends it by hand. */
export interface FakeSound {
    readonly sound: Sound;
    readonly ended: Promise<PlaybackEnd>;
    start(): Promise<void>;
    end(end: PlaybackEnd): Promise<void>;
}

export interface FakePlayer {
    readonly sounds: FakeSound[];
    readonly stoppers: (() => Promise<void>)[];
    readonly mutes: (() => Promise<void>)[];
    closed: boolean;
    muted: boolean;
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
        const fake: FakePlayer = { sounds: [], stoppers: [], mutes: [], closed: false, muted: false };
        created.push(fake);
        return {
            play: (sound) => {
                const started = Promise.withResolvers<PlaybackStart>();
                const ended = Promise.withResolvers<PlaybackEnd>();
                const end = async (how: PlaybackEnd) => {
                    started.resolve({ kind: "ended" });
                    ended.resolve(how);
                    await settled();
                };
                fake.sounds.push({
                    sound,
                    ended: ended.promise,
                    start: async () => {
                        started.resolve({ kind: "playing", contextTime: 0, context: {} as AudioContext });
                        await settled();
                    },
                    end,
                });
                fake.stoppers.push(() => end({ kind: "stopped", playedSeconds: 0 }));
                fake.mutes.push(() => end({ kind: "muted", playedSeconds: 0 }));
                if (fake.muted) void end({ kind: "muted", playedSeconds: 0 });
                return { started: started.promise, ended: ended.promise };
            },
            // Like the player: every sound handed over so far ends stopped, one already ended staying as it ended.
            stop: () => {
                for (const stopSound of fake.stoppers.splice(0)) void stopSound();
            },
            // Like the player: muting ends every sound handed over so far muted, and a sound handed over while muted ends muted at once.
            setMuted: (muted) => {
                fake.muted = muted;
                if (muted) for (const muteSound of fake.mutes.splice(0)) void muteSound();
            },
            close: () => {
                fake.closed = true;
            },
        };
    };
    return { created, createPlayer };
}
