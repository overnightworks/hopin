// Safari before 14.1 only has the prefixed constructor.
const audioHost: { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext } = globalThis;
const AudioContextConstructor = (audioHost.AudioContext || audioHost.webkitAudioContext) as typeof AudioContext;
const audioContext = new AudioContextConstructor();

const TONE_GAIN = 0.15;
const TONE_FADE_FLOOR = 0.001;
const JOIN_TONES_HZ = [660, 880] as const;
const LEAVE_TONES_HZ = [440, 330] as const;
const MESSAGE_TONE_HZ = 800;
const TONE_SECONDS = 0.15;
const LAST_LEAVE_TONE_SECONDS = 0.2;
const MESSAGE_TONE_SECONDS = 0.1;
const SECOND_TONE_DELAY_MS = 100;

function playTone(frequency: number, durationSeconds: number): void {
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

export function playJoinSound(): void {
    const [first, second] = JOIN_TONES_HZ;
    playTone(first, TONE_SECONDS);
    setTimeout(() => {
        playTone(second, TONE_SECONDS);
    }, SECOND_TONE_DELAY_MS);
}

export function playLeaveSound(): void {
    const [first, second] = LEAVE_TONES_HZ;
    playTone(first, TONE_SECONDS);
    setTimeout(() => {
        playTone(second, LAST_LEAVE_TONE_SECONDS);
    }, SECOND_TONE_DELAY_MS);
}

export function playMessageSound(): void {
    playTone(MESSAGE_TONE_HZ, MESSAGE_TONE_SECONDS);
}
