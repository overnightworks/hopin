const VIDEO_UPLOAD_BUDGET_BPS = 2_000_000;

export function videoBitrateCap(remoteCount) {
    return Math.floor(VIDEO_UPLOAD_BUDGET_BPS / remoteCount);
}

function videoSenders(connection) {
    return connection
        .getTransceivers()
        .filter((transceiver) => transceiver.receiver.track.kind === "video")
        .map((transceiver) => transceiver.sender);
}

async function capSender(sender, maxBitrate) {
    const parameters = sender.getParameters();
    parameters.encodings[0].maxBitrate = maxBitrate;
    await sender.setParameters(parameters);
}

export async function applyUploadCaps(connections) {
    if (connections.length === 0) return;
    const maxBitrate = videoBitrateCap(connections.length);
    const senders = connections.flatMap(videoSenders);
    await Promise.all(senders.map((sender) => capSender(sender, maxBitrate)));
}
