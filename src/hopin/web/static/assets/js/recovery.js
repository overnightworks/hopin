export const ICE_DISCONNECTED_GRACE_MS = 2000;
const ICE_RESTART_RETRY_DELAYS_MS = [2000, 4000, 8000, 15000];
const CONNECTED_STATES = new Set(["connected", "completed"]);
const LOST_STATES = new Set(["disconnected", "failed"]);

export function restartDelay(attempt) {
    return ICE_RESTART_RETRY_DELAYS_MS[Math.min(attempt, ICE_RESTART_RETRY_DELAYS_MS.length - 1)];
}

export function watchIceConnection(connection, restart) {
    let attempt = 0;
    let episodeBegun = false;
    let pendingTimer = null;

    const isClosed = () => connection.signalingState === "closed";
    const isConnected = () => CONNECTED_STATES.has(connection.iceConnectionState);

    function schedule(delayMs, callback) {
        clearTimeout(pendingTimer);
        pendingTimer = setTimeout(() => {
            pendingTimer = null;
            if (!isClosed()) callback();
        }, delayMs);
    }

    function endEpisode() {
        clearTimeout(pendingTimer);
        pendingTimer = null;
        attempt = 0;
        episodeBegun = false;
    }

    function restartAndRetry() {
        if (isConnected()) {
            endEpisode();
            return;
        }
        if (!episodeBegun && connection.signalingState !== "stable") {
            schedule(restartDelay(0), restartAndRetry);
            return;
        }
        episodeBegun = true;
        void restart();
        schedule(restartDelay(attempt), restartAndRetry);
        attempt += 1;
    }

    function restartAfterGrace() {
        if (LOST_STATES.has(connection.iceConnectionState)) restartAndRetry();
    }

    connection.addEventListener("iceconnectionstatechange", () => {
        const state = connection.iceConnectionState;
        if (CONNECTED_STATES.has(state)) {
            endEpisode();
        } else if (episodeBegun) {
            return;
        } else if (state === "failed") {
            restartAndRetry();
        } else if (state === "disconnected" && pendingTimer === null) {
            schedule(ICE_DISCONNECTED_GRACE_MS, restartAfterGrace);
        }
    });
}
