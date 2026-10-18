const POLL_INTERVAL_MS = 3000;
const BAR_HEIGHTS_PX = [4, 8, 12, 16];
const ACTIVE_BARS_BY_QUALITY = { excellent: 4, good: 3, poor: 2, bad: 1 };

export function createQualityIndicator() {
    const indicator = document.createElement("div");
    indicator.className = "connection-quality";
    for (const height of BAR_HEIGHTS_PX) {
        const bar = document.createElement("div");
        bar.className = "quality-bar";
        bar.style.height = `${height}px`;
        indicator.appendChild(bar);
    }
    return indicator;
}

function barColor(activeBars) {
    if (activeBars >= 3) return "#4ade80";
    if (activeBars === 2) return "#fbbf24";
    return "#ef4444";
}

function updateQualityIndicator(container, quality) {
    const indicator = container.querySelector(".connection-quality");
    if (!indicator) return;
    const activeBars = ACTIVE_BARS_BY_QUALITY[quality] || 1;
    const color = barColor(activeBars);
    for (const [index, bar] of indicator.querySelectorAll(".quality-bar").entries()) {
        if (index < activeBars) {
            bar.className = "quality-bar";
            bar.style.background = color;
        } else {
            bar.className = "quality-bar inactive";
            bar.style.background = "";
        }
    }
}

function classifyRoundTripTime(roundTripSeconds) {
    if (roundTripSeconds < 0.1) return "excellent";
    if (roundTripSeconds < 0.3) return "good";
    if (roundTripSeconds < 0.5) return "poor";
    return "bad";
}

function applyQualityReport(container, report) {
    if (report.type !== "candidate-pair" || report.state !== "succeeded") return;
    const roundTripSeconds = report.currentRoundTripTime;
    if (roundTripSeconds === undefined) return;
    updateQualityIndicator(container, classifyRoundTripTime(roundTripSeconds));
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
