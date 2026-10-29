const POLL_INTERVAL_MS = 3000;
const BAR_COUNT = 3;
const GOOD_ROUND_TRIP_SECONDS = 0.3;
const FAIR_ROUND_TRIP_SECONDS = 0.5;
const QUALITY = { GOOD: "good", FAIR: "fair", POOR: "poor" };

export function createQualityIndicator() {
    const indicator = document.createElement("div");
    indicator.className = "connection-quality";
    for (let index = 0; index < BAR_COUNT; index++) {
        const bar = document.createElement("div");
        bar.className = "quality-bar";
        indicator.appendChild(bar);
    }
    return indicator;
}

function updateQualityIndicator(container, quality) {
    const indicator = container.querySelector(".connection-quality");
    if (!indicator) return;
    indicator.dataset.quality = quality;
}

export function qualityOfRoundTrip(roundTripSeconds) {
    if (roundTripSeconds < GOOD_ROUND_TRIP_SECONDS) return QUALITY.GOOD;
    if (roundTripSeconds < FAIR_ROUND_TRIP_SECONDS) return QUALITY.FAIR;
    return QUALITY.POOR;
}

function applyQualityReport(container, report) {
    if (report.type !== "candidate-pair" || report.state !== "succeeded") return;
    const roundTripSeconds = report.currentRoundTripTime;
    if (roundTripSeconds === undefined) return;
    updateQualityIndicator(container, qualityOfRoundTrip(roundTripSeconds));
}

function pollConnectionQuality(participantId, connection, interval) {
    if (connection.connectionState === "closed") {
        clearInterval(interval);
        return;
    }
    const container = document.getElementById(`container-${participantId}`);
    if (!container) {
        clearInterval(interval);
        return;
    }
    connection.getStats().then((stats) => {
        for (const report of stats.values()) {
            applyQualityReport(container, report);
        }
    });
}

export function monitorConnectionQuality(participantId, connection) {
    const interval = setInterval(() => {
        pollConnectionQuality(participantId, connection, interval);
    }, POLL_INTERVAL_MS);
}
