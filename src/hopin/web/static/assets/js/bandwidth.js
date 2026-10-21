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

async function setCap(sender, maxBitrate) {
    const parameters = sender.getParameters();
    parameters.encodings[0].maxBitrate = maxBitrate;
    await sender.setParameters(parameters);
}

// A sender accepts one getParameters/setParameters transaction at a time: a second
// setParameters after another one consumed the transaction is rejected. Caps on one
// sender therefore run in turn, each reading its parameters right before it sets them.
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
