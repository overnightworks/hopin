const audioContext = new (globalThis.AudioContext || globalThis.webkitAudioContext)();

const SECOND_TONE_DELAY_MS = 100;

function playTone(frequency, durationSeconds) {
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequency;
    gain.gain.value = 0.15;
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + durationSeconds);
    oscillator.stop(audioContext.currentTime + durationSeconds);
}

export function playJoinSound() {
    playTone(660, 0.15);
    setTimeout(() => playTone(880, 0.15), SECOND_TONE_DELAY_MS);
}

export function playLeaveSound() {
    playTone(440, 0.15);
    setTimeout(() => playTone(330, 0.2), SECOND_TONE_DELAY_MS);
}

export function playMessageSound() {
    playTone(800, 0.1);
}
