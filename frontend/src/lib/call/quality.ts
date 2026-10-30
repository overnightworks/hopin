import { CONNECTION_QUALITY, setConnectionQuality, type ConnectionQuality } from "./stage-port.ts";

const POLL_INTERVAL_MS = 3000;
const GOOD_ROUND_TRIP_SECONDS = 0.3;
const FAIR_ROUND_TRIP_SECONDS = 0.5;

export type QualityConnection = Pick<RTCPeerConnection, "connectionState" | "getStats">;

interface QualityReport {
    type: string;
    state?: string;
    currentRoundTripTime?: number;
}

export function qualityOfRoundTrip(roundTripSeconds: number): ConnectionQuality {
    if (roundTripSeconds < GOOD_ROUND_TRIP_SECONDS) return CONNECTION_QUALITY.GOOD;
    if (roundTripSeconds < FAIR_ROUND_TRIP_SECONDS) return CONNECTION_QUALITY.FAIR;
    return CONNECTION_QUALITY.POOR;
}

function applyQualityReport(participantId: string, report: QualityReport): void {
    if (report.type !== "candidate-pair" || report.state !== "succeeded") return;
    const roundTripSeconds = report.currentRoundTripTime;
    if (roundTripSeconds === undefined) return;
    setConnectionQuality(participantId, qualityOfRoundTrip(roundTripSeconds));
}

function pollConnectionQuality(
    participantId: string,
    connection: QualityConnection,
    interval: ReturnType<typeof setInterval>,
): void {
    if (connection.connectionState === "closed") {
        clearInterval(interval);
        return;
    }
    void connection.getStats().then((stats) => {
        for (const report of stats.values() as Iterable<QualityReport>) {
            applyQualityReport(participantId, report);
        }
    });
}

// Stops with the peer's lifecycle: the mesh closes the connection before it removes the tile.
export function monitorConnectionQuality(participantId: string, connection: QualityConnection): void {
    const interval = setInterval(() => {
        pollConnectionQuality(participantId, connection, interval);
    }, POLL_INTERVAL_MS);
}
