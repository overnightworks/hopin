import { setRemoteSpeaking } from "./stage.js";

const SPEAKING_POLL_INTERVAL_MS = 100;
// The browser keeps the last level of a source whose sound stopped arriving, so only a
// level measured within the last few packets counts.
const SOUND_FRESHNESS_MS = 300;
// About -40 dBov: above the room noise of a quiet microphone, below a spoken syllable.
const SPEAKING_LEVEL_FLOOR = 0.01;
// Bridges the pauses between words, so the ring does not flicker.
const SPEAKING_HOLD_MS = 800;

function loudestFreshLevel(sources, now) {
    return sources
        .filter((source) => now - source.timestamp <= SOUND_FRESHNESS_MS)
        .reduce((loudest, source) => Math.max(loudest, source.audioLevel), 0);
}

function isLoud(sources, now) {
    return loudestFreshLevel(sources, now) >= SPEAKING_LEVEL_FLOOR;
}

export function isSpeaking(sources, now, lastLoudTime) {
    return isLoud(sources, now) || now - lastLoudTime < SPEAKING_HOLD_MS;
}

function arrivingAudioSources(connection) {
    return connection
        .getReceivers()
        .filter((receiver) => receiver.track.kind === "audio")
        .flatMap((receiver) => receiver.getSynchronizationSources());
}

export function monitorSpeaking(participantId, connection) {
    let lastLoudTime = -Infinity;
    let speaking = false;
    const interval = setInterval(() => {
        if (connection.connectionState === "closed" || !document.getElementById(`container-${participantId}`)) {
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
