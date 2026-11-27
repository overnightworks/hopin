import type { Playback, Player, PlaybackEnd, PlaybackStart, Sound } from "overnightworks-speech-browser";

/** A sound the player was handed; the test starts it and ends it by hand. */
export interface FakeSound {
    readonly sound: Sound;
    readonly ended: Promise<PlaybackEnd>;
    /** Starts the sound; while the player pauses it waits for the resume, unless it starts inside the pause's ramp. */
    start(options?: { inRamp?: boolean }): Promise<void>;
    end(end: PlaybackEnd): Promise<void>;
}

export interface FakePlayer {
    readonly tap: AudioNode;
    readonly sounds: FakeSound[];
    readonly stoppers: (() => Promise<void>)[];
    readonly mutes: (() => Promise<void>)[];
    /** What a pause holds back: a sound's start, or the end of one that plays on, until `resume`. */
    readonly held: (() => Promise<void>)[];
    closed: boolean;
    muted: boolean;
    paused: boolean;
    /** What the player's playbacks read as played seconds; the test sets it. */
    playedSeconds: number;
}

// The feature reads a playback one promise turn after it resolves.
async function settled() {
    await Promise.resolve();
    await Promise.resolve();
}

/** The playback a fake player hands over for a sound; its start and end are the test's. */
function playOn(fake: FakePlayer, sound: Sound): Playback {
    const started = Promise.withResolvers<PlaybackStart>();
    const ended = Promise.withResolvers<PlaybackEnd>();
    const end = async (how: PlaybackEnd) => {
        started.resolve({ kind: "ended" });
        ended.resolve(how);
        await settled();
    };
    const whenRunning = async (step: () => Promise<void>) => {
        if (fake.paused) fake.held.push(step);
        else await step();
    };
    fake.sounds.push({
        sound,
        ended: ended.promise,
        start: (options) => {
            const begin = async () => {
                started.resolve({ kind: "playing", contextTime: 0, context: {} as AudioContext });
                await settled();
            };
            return options?.inRamp ? begin() : whenRunning(begin);
        },
        end: (how) => whenRunning(() => end(how)),
    });
    fake.stoppers.push(() => end({ kind: "stopped", playedSeconds: 0 }));
    fake.mutes.push(() => end({ kind: "muted", playedSeconds: 0 }));
    if (fake.muted) void end({ kind: "muted", playedSeconds: 0 });
    return { started: started.promise, ended: ended.promise, playedSeconds: () => fake.playedSeconds };
}

/** The players a feature created, in order, and the `createPlayer` that creates them. Like the player, a pause holds every sound where it is: a sound handed over meanwhile starts only at `resume`, one that plays does not play out, and a stop ends them at once and leaves the pause standing. It runs no sound to a position, so a hold the app asks for with `pauseAt` never comes. */
export function fakePlayers() {
    const created: FakePlayer[] = [];
    const createPlayer = (): Player => {
        const fake: FakePlayer = {
            tap: {} as AudioNode,
            sounds: [],
            stoppers: [],
            mutes: [],
            held: [],
            closed: false,
            muted: false,
            paused: false,
            playedSeconds: 0,
        };
        created.push(fake);
        return {
            tap: fake.tap,
            play: (sound) => playOn(fake, sound),
            // Like the player: every sound handed over so far ends stopped, one already ended staying as it ended.
            stop: () => {
                for (const stopSound of fake.stoppers.splice(0)) void stopSound();
            },
            // Like the player: muting ends every sound handed over so far muted, clears a pause, and a sound handed over while muted ends muted at once.
            setMuted: (muted) => {
                fake.muted = muted;
                if (!muted) return;
                fake.paused = false;
                fake.held.length = 0;
                for (const muteSound of fake.mutes.splice(0)) void muteSound();
            },
            pauseAt: () => Promise.resolve({ kind: "overtaken" }),
            pause: () => {
                fake.paused = true;
            },
            // Like the player: the sounds the pause held back go on, in the order they were held.
            resume: () => {
                fake.paused = false;
                for (const step of fake.held.splice(0)) void step();
            },
            close: () => {
                fake.closed = true;
            },
        };
    };
    return { created, createPlayer };
}
