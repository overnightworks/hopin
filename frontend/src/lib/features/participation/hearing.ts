import { HopClose, type Capture, type CaptureOptions } from "overnightworks-speech-browser";
import workletUrl from "overnightworks-speech-browser/capture-worklet.js?url&no-inline";

type StartCapture = (options: CaptureOptions) => Capture;

/** What the person's browser sends of their microphone: one capture of the stream into the seat's hop while the server's record has an agent hearing them and the core holds their microphone, and none otherwise. */
export interface HearingCapture {
    setHeard(heard: boolean): void;
    setStream(stream: MediaStream | null): void;
}

/** `hopUrl` is read when a capture starts and `startCapture` starts it; the stream's capture stops the moment either input ends. A hop that ends past its deadline is opened once more; every other end leaves the capture closed until the next change of the switch or the stream. */
export function createHearingCapture(hopUrl: () => string, startCapture: StartCapture): HearingCapture {
    let heard = false;
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
            if (end.kind === "hop" && end.close === HopClose.PAST_DEADLINE && mayReopen) {
                mayReopen = false;
                reconcile();
            }
        });
    }

    function reconcile() {
        stop();
        if (heard && stream !== null) start(stream);
    }

    return {
        setHeard(value) {
            if (value === heard) return;
            heard = value;
            mayReopen = true;
            reconcile();
        },
        setStream(value) {
            if (value === stream) return;
            stream = value;
            mayReopen = true;
            reconcile();
        },
    };
}
