import { HopClose, type Capture, type CaptureOptions } from "overnightworks-speech-browser";
import workletUrl from "overnightworks-speech-browser/capture-worklet.js?url&no-inline";

const REOPENED_ENDS: ReadonlySet<HopClose> = new Set([HopClose.PAST_DEADLINE, HopClose.NORMAL]);

type StartCapture = (options: CaptureOptions) => Capture;

/** What the person's browser sends of their microphone: one capture of the stream into the seat's hop while the server's record has an agent hearing them and the core holds their microphone, and none otherwise. */
export interface HearingCapture {
    /** The number of agents the server's record has hearing the person. */
    setHearers(count: number): void;
    setStream(stream: MediaStream | null): void;
}

/** `hopUrl` is read when a capture starts and `startCapture` starts it; the stream's capture stops the moment either input ends. A hop that ends past its deadline or in order, as it does with the agent that serves it, is opened once more; every other end leaves the capture closed until the next change of the switch or the stream. Each change of the number of agents that hear the person allows one more reopening, and opens a capture that an earlier end left closed while an agent still hears the person. */
export function createHearingCapture(hopUrl: () => string, startCapture: StartCapture): HearingCapture {
    let hearers = 0;
    let stream: MediaStream | null = null;
    let running: Capture | null = null;
    let mayReopen = true;

    function stop() {
        running?.stop();
        running = null;
    }

    function start(microphone: MediaStream) {
        const capture = startCapture({ stream: microphone, hopUrl: hopUrl(), workletUrl });
        running = capture;
        void capture.ended.then((end) => {
            if (running !== capture) return;
            running = null;
            if (end.kind === "hop" && REOPENED_ENDS.has(end.close) && mayReopen) {
                mayReopen = false;
                reconcile();
            }
        });
    }

    function reconcile() {
        stop();
        if (hearers > 0 && stream !== null) start(stream);
    }

    return {
        setHearers(count) {
            if (count === hearers) return;
            const wasHeard = hearers > 0;
            hearers = count;
            mayReopen = true;
            if (wasHeard !== hearers > 0 || running === null) reconcile();
        },
        setStream(value) {
            if (value === stream) return;
            stream = value;
            mayReopen = true;
            reconcile();
        },
    };
}
