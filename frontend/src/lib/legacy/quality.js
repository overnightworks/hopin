import { CONNECTION_QUALITY, hasTile, setConnectionQuality } from "#lib/call/stage-port.ts";

const POLL_INTERVAL_MS = 3000;
const GOOD_ROUND_TRIP_SECONDS = 0.3;
const FAIR_ROUND_TRIP_SECONDS = 0.5;

export function qualityOfRoundTrip(roundTripSeconds) {
    if (roundTripSeconds < GOOD_ROUND_TRIP_SECONDS) return CONNECTION_QUALITY.GOOD;
    if (roundTripSeconds < FAIR_ROUND_TRIP_SECONDS) return CONNECTION_QUALITY.FAIR;
    return CONNECTION_QUALITY.POOR;
}

function applyQualityReport(participantId, report) {
    if (report.type !== "candidate-pair" || report.state !== "succeeded") return;
    const roundTripSeconds = report.currentRoundTripTime;
    if (roundTripSeconds === undefined) return;
    setConnectionQuality(participantId, qualityOfRoundTrip(roundTripSeconds));
}

function pollConnectionQuality(participantId, connection, interval) {
    if (connection.connectionState === "closed" || !hasTile(participantId)) {
        clearInterval(interval);
        return;
    }
    connection.getStats().then((stats) => {
        for (const report of stats.values()) {
            applyQualityReport(participantId, report);
        }
    });
}

export function monitorConnectionQuality(participantId, connection) {
    const interval = setInterval(() => {
        pollConnectionQuality(participantId, connection, interval);
    }, POLL_INTERVAL_MS);
}
