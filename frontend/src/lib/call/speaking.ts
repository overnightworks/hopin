import { setRemoteSpeaking } from "./stage-port.ts";

const SPEAKING_POLL_INTERVAL_MS = 100;
// The browser keeps the last level of a source whose sound stopped arriving, so only a
// level measured within the last few packets counts.
const SOUND_FRESHNESS_MS = 300;
// About -40 dBov: above the room noise of a quiet microphone, below a spoken syllable.
const SPEAKING_LEVEL_FLOOR = 0.01;
// Bridges the pauses between words, so the ring does not flicker.
const SPEAKING_HOLD_MS = 800;

export interface SoundSource {
    audioLevel: number;
    timestamp: number;
}

interface SoundReceiver {
    track: Pick<MediaStreamTrack, "kind">;
    getSynchronizationSources(): SoundSource[];
}

export type MonitoredConnection = Pick<RTCPeerConnection, "connectionState"> & {
    getReceivers(): SoundReceiver[];
};

function loudestFreshLevel(sources: SoundSource[], now: number): number {
    return sources
        .filter((source) => now - source.timestamp <= SOUND_FRESHNESS_MS)
        .reduce((loudest, source) => Math.max(loudest, source.audioLevel), 0);
}

function isLoud(sources: SoundSource[], now: number): boolean {
    return loudestFreshLevel(sources, now) >= SPEAKING_LEVEL_FLOOR;
}

export function isSpeaking(sources: SoundSource[], now: number, lastLoudTime: number): boolean {
    return isLoud(sources, now) || now - lastLoudTime < SPEAKING_HOLD_MS;
}

function arrivingAudioSources(connection: MonitoredConnection): SoundSource[] {
    return connection
        .getReceivers()
        .filter((receiver) => receiver.track.kind === "audio")
        .flatMap((receiver) => receiver.getSynchronizationSources());
}

// Stops with the peer's lifecycle: the mesh closes the connection before it removes the tile.
export function monitorSpeaking(participantId: string, connection: MonitoredConnection): void {
    let lastLoudTime = -Infinity;
    let speaking = false;
    const interval = setInterval(() => {
        if (connection.connectionState === "closed") {
            clearInterval(interval);
            return;
        }
        const sources = arrivingAudioSources(connection);
        const now = performance.timeOrigin + performance.now();
        if (isLoud(sources, now)) lastLoudTime = now;
        const speaksNow = isSpeaking(sources, now, lastLoudTime);
        if (speaksNow === speaking) return;
        speaking = speaksNow;
        setRemoteSpeaking(participantId, speaking);
    }, SPEAKING_POLL_INTERVAL_MS);
}
