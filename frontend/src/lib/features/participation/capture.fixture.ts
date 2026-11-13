import type { Capture, CaptureEnd, CaptureOptions } from "overnightworks-speech-browser";

/** A capture that the test ends by hand: `stopped` once the feature stopped it, `over` once it ended by itself. */
export interface FakeCapture {
    readonly options: CaptureOptions;
    stopped: boolean;
    over: boolean;
    end(cause: CaptureEnd): Promise<void>;
}

/** The captures a feature started, in order, and the `startCapture` that starts them. */
export function fakeCaptures() {
    const started: FakeCapture[] = [];
    const startCapture = (options: CaptureOptions): Capture => {
        const ended = Promise.withResolvers<CaptureEnd>();
        const fake: FakeCapture = {
            options,
            stopped: false,
            over: false,
            end: async (cause) => {
                fake.over = true;
                ended.resolve(cause);
                // The feature reads the end one promise turn after it resolves.
                await Promise.resolve();
                await Promise.resolve();
            },
        };
        started.push(fake);
        return {
            started: Promise.resolve("streaming"),
            ended: ended.promise,
            stop: () => {
                fake.stopped = true;
            },
        };
    };
    return {
        started,
        startCapture,
        live: () => started.filter((capture) => !capture.stopped && !capture.over),
        clear: () => {
            started.length = 0;
        },
    };
}
