// The speaking ring: each peer's and the own sound level, polled until the connection closes and reported
// through the stage port.

import { onMicrophone } from "./microphone.ts";
import { setOwnSpeaking, setRemoteSpeaking } from "./stage-port.ts";

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

// Hands on a change of whether the sound speaks, measured each poll, with the hold applied.
function speakingTracker(report: (speaking: boolean) => void) {
    let lastLoudTime = -Infinity;
    let speaking = false;
    return (sources: SoundSource[], now: number) => {
        if (isLoud(sources, now)) lastLoudTime = now;
        const speaksNow = isSpeaking(sources, now, lastLoudTime);
        if (speaksNow === speaking) return;
        speaking = speaksNow;
        report(speaking);
    };
}

const currentTime = () => performance.timeOrigin + performance.now();

// Stops with the peer's lifecycle: the mesh closes the connection before it removes the tile.
export function monitorSpeaking(participantId: string, connection: MonitoredConnection): void {
    const track = speakingTracker((speaking) => {
        setRemoteSpeaking(participantId, speaking);
    });
    const interval = setInterval(() => {
        if (connection.connectionState === "closed") {
            clearInterval(interval);
            return;
        }
        track(arrivingAudioSources(connection), currentTime());
    }, SPEAKING_POLL_INTERVAL_MS);
}

// The root mean square of the samples, on the same linear scale as a received source's audio level.
function soundLevel(samples: Float32Array): number {
    const power = samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length;
    return Math.sqrt(power);
}

// No connection reports the level of the person's own sound, so the page measures their microphone itself; a context
// the browser keeps suspended reads silence.
function monitorMicrophone(stream: MediaStream): () => void {
    const context = new AudioContext();
    const analyser = context.createAnalyser();
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    const track = speakingTracker(setOwnSpeaking);
    const interval = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const now = currentTime();
        track([{ audioLevel: soundLevel(samples), timestamp: now }], now);
    }, SPEAKING_POLL_INTERVAL_MS);
    return () => {
        clearInterval(interval);
        void context.close();
        setOwnSpeaking(false);
    };
}

/** Tells the stage whether the person speaks, from their own microphone while the call core holds it; without it they are silent. */
export function monitorOwnSpeaking(): void {
    let stopMonitoring: (() => void) | null = null;
    onMicrophone((stream) => {
        stopMonitoring?.();
        stopMonitoring = stream === null ? null : monitorMicrophone(stream);
    });
}
