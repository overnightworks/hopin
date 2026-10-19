const audioContext = new (globalThis.AudioContext || globalThis.webkitAudioContext)();

const TONE_GAIN = 0.15;
const TONE_FADE_FLOOR = 0.001;
const JOIN_TONES_HZ = [660, 880];
const LEAVE_TONES_HZ = [440, 330];
const MESSAGE_TONE_HZ = 800;
const TONE_SECONDS = 0.15;
const LAST_LEAVE_TONE_SECONDS = 0.2;
const MESSAGE_TONE_SECONDS = 0.1;
const SECOND_TONE_DELAY_MS = 100;

function playTone(frequency, durationSeconds) {
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequency;
    gain.gain.value = TONE_GAIN;
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    gain.gain.exponentialRampToValueAtTime(TONE_FADE_FLOOR, audioContext.currentTime + durationSeconds);
    oscillator.stop(audioContext.currentTime + durationSeconds);
}

export function playJoinSound() {
    const [first, second] = JOIN_TONES_HZ;
    playTone(first, TONE_SECONDS);
    setTimeout(() => playTone(second, TONE_SECONDS), SECOND_TONE_DELAY_MS);
}

export function playLeaveSound() {
    const [first, second] = LEAVE_TONES_HZ;
    playTone(first, TONE_SECONDS);
    setTimeout(() => playTone(second, LAST_LEAVE_TONE_SECONDS), SECOND_TONE_DELAY_MS);
}

export function playMessageSound() {
    playTone(MESSAGE_TONE_HZ, MESSAGE_TONE_SECONDS);
}
