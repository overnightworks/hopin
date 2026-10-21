const VIDEO_UPLOAD_BUDGET_BPS = 2_000_000;

export function videoBitrateCap(remoteCount) {
    return Math.floor(VIDEO_UPLOAD_BUDGET_BPS / remoteCount);
}

export function videoTransceivers(connection) {
    return connection.getTransceivers().filter((transceiver) => transceiver.receiver.track.kind === "video");
}

function videoSenders(connection) {
    return videoTransceivers(connection).map((transceiver) => transceiver.sender);
}

const lastCapBySender = new WeakMap();

const CAP_ATTEMPTS = 3;

// Chromium rejected a cap with an InvalidStateError ("getParameters() has never been
// called") while caps overlapped a camera or screen track change; CI showed it in the
// C9(c) camera-off case. So the cap is read and set again, up to CAP_ATTEMPTS. That is
// an empirical retry budget, not a guaranteed bound: after the last attempt the error
// is thrown. Other errors are thrown at once.
async function setCap(sender, maxBitrate) {
    for (let attempt = 1; ; attempt++) {
        const parameters = sender.getParameters();
        // A sender of a closed connection reports no encodings: its peer left while this
        // cap waited its turn, so there is nothing left to cap.
        if (parameters.encodings.length === 0) return;
        parameters.encodings[0].maxBitrate = maxBitrate;
        try {
            await sender.setParameters(parameters);
            return;
        } catch (error) {
            if (error.name !== "InvalidStateError" || attempt === CAP_ATTEMPTS) throw error;
        }
    }
}

// Caps on one sender run one after another, each reading its parameters right before
// it sets them, so the latest bitrate is applied last.
function capSender(sender, maxBitrate) {
    const previous = lastCapBySender.get(sender) ?? Promise.resolve();
    const cap = previous.then(() => setCap(sender, maxBitrate));
    // The caller still sees this cap's failure; the queue only waits for it to end.
    lastCapBySender.set(sender, cap.catch(() => {}));
    return cap;
}

export async function applyUploadCaps(connections) {
    const maxBitrate = videoBitrateCap(connections.length);
    const senders = connections.flatMap(videoSenders);
    await Promise.all(senders.map((sender) => capSender(sender, maxBitrate)));
}
