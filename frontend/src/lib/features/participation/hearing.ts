import { HopClose, startCapture, type Capture } from "overnightworks-speech-browser";
import workletUrl from "overnightworks-speech-browser/capture-worklet.js?url";

interface Running {
    readonly capture: Capture;
    readonly stream: MediaStream;
}

/** What the person's browser sends of their microphone: one capture of the stream into the seat's hop while the server's record has an agent hearing them and the core holds their microphone, and none otherwise. */
export interface HearingCapture {
    setHeard(heard: boolean): void;
    setStream(stream: MediaStream | null): void;
}

/** `hopUrl` is read when a capture starts; the stream's capture stops the moment either input ends. A hop that ends past its deadline is opened once more; every other end leaves the capture closed until the next change. */
export function createHearingCapture(hopUrl: () => string): HearingCapture {
    let heard = false;
    let stream: MediaStream | null = null;
    let running: Running | null = null;
    let mayReopen = true;

    function stop() {
        running?.capture.stop();
        running = null;
    }

    function start(microphone: MediaStream) {
        const capture = startCapture({ stream: microphone, hopUrl: hopUrl(), workletUrl });
        const own: Running = { capture, stream: microphone };
        running = own;
        void capture.ended.then((end) => {
            if (running !== own) return;
            running = null;
            if (end.kind === "hop" && end.close === HopClose.PAST_DEADLINE && mayReopen) {
                mayReopen = false;
                reconcile();
            }
        });
    }

    function reconcile() {
        if (!heard || stream === null) {
            stop();
        } else if (running?.stream !== stream) {
            stop();
            start(stream);
        }
    }

    return {
        setHeard(value) {
            heard = value;
            mayReopen = true;
            reconcile();
        },
        setStream(value) {
            stream = value;
            mayReopen = true;
            reconcile();
        },
    };
}
