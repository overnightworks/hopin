const VIDEO_UPLOAD_BUDGET_BPS = 2_000_000;

export type CappedConnection = Pick<RTCPeerConnection, "getTransceivers" | "signalingState">;

interface VideoSender {
    readonly sender: RTCRtpSender;
    readonly connection: CappedConnection;
}

export function videoBitrateCap(remoteCount: number): number {
    return Math.floor(VIDEO_UPLOAD_BUDGET_BPS / remoteCount);
}

export function videoTransceivers(connection: CappedConnection): RTCRtpTransceiver[] {
    return connection.getTransceivers().filter((transceiver) => transceiver.receiver.track.kind === "video");
}

function videoSenders(connection: CappedConnection): VideoSender[] {
    return videoTransceivers(connection).map((transceiver) => ({ sender: transceiver.sender, connection }));
}

const lastCapBySender = new WeakMap<RTCRtpSender, Promise<void>>();

const CAP_ATTEMPTS = 3;

// Chromium rejected a cap with an InvalidStateError ("getParameters() has never been
// called") while caps overlapped a camera or screen track change; CI showed it in the
// C9(c) camera-off case. So the cap is read and set again, up to CAP_ATTEMPTS. That is
// an empirical retry budget, not a guaranteed bound: after the last attempt the error
// is thrown. Other errors are thrown at once. A closed connection has nothing left to
// cap (its peer left while this cap waited its turn, or this person is leaving), and
// engines differ in what its senders still report, so its state decides, never the
// parameters.
async function setCap({ sender, connection }: VideoSender, maxBitrate: number): Promise<void> {
    for (let attempt = 1; ; attempt++) {
        if (connection.signalingState === "closed") return;
        const parameters = sender.getParameters();
        (parameters.encodings[0] as RTCRtpEncodingParameters).maxBitrate = maxBitrate;
        try {
            await sender.setParameters(parameters);
            return;
        } catch (error) {
            if ((error as DOMException).name !== "InvalidStateError" || attempt === CAP_ATTEMPTS) throw error;
        }
    }
}

// Caps on one sender run one after another, each reading its parameters right before
// it sets them, so the latest bitrate is applied last.
function capSender(videoSender: VideoSender, maxBitrate: number): Promise<void> {
    const previous = lastCapBySender.get(videoSender.sender) ?? Promise.resolve();
    const cap = previous.then(() => setCap(videoSender, maxBitrate));
    // The caller still sees this cap's failure; the queue only waits for it to end.
    lastCapBySender.set(
        videoSender.sender,
        cap.catch(() => {}),
    );
    return cap;
}

export async function applyUploadCaps(connections: CappedConnection[]): Promise<void> {
    const maxBitrate = videoBitrateCap(connections.length);
    const senders = connections.flatMap(videoSenders);
    await Promise.all(senders.map((videoSender) => capSender(videoSender, maxBitrate)));
}
