export const ICE_DISCONNECTED_GRACE_MS = 2000;
const ICE_RESTART_RETRY_DELAYS_MS = [2000, 4000, 8000, 15000];
const CONNECTED_STATES = new Set<RTCIceConnectionState>(["connected", "completed"]);
const LOST_STATES = new Set<RTCIceConnectionState>(["disconnected", "failed"]);

export type WatchedConnection = Pick<RTCPeerConnection, "addEventListener" | "iceConnectionState" | "signalingState">;

export function restartDelay(attempt: number): number {
    return ICE_RESTART_RETRY_DELAYS_MS[Math.min(attempt, ICE_RESTART_RETRY_DELAYS_MS.length - 1)] as number;
}

export function watchIceConnection(connection: WatchedConnection, restart: () => unknown): void {
    let attempt = 0;
    let episodeBegun = false;
    let pendingTimer: ReturnType<typeof setTimeout> | null = null;

    const isClosed = () => connection.signalingState === "closed";
    const isConnected = () => CONNECTED_STATES.has(connection.iceConnectionState);

    function schedule(delayMs: number, callback: () => void): void {
        clearTimeout(pendingTimer ?? undefined);
        pendingTimer = setTimeout(() => {
            pendingTimer = null;
            if (!isClosed()) callback();
        }, delayMs);
    }

    function endEpisode(): void {
        clearTimeout(pendingTimer ?? undefined);
        pendingTimer = null;
        attempt = 0;
        episodeBegun = false;
    }

    function restartAndRetry(): void {
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

    function restartAfterGrace(): void {
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
