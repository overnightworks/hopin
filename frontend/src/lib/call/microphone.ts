import { state } from "#lib/call/state.ts";

type MicrophoneHandler = (stream: MediaStream | null) => void;

const handlers: MicrophoneHandler[] = [];
let handed: { track: MediaStreamTrack; stream: MediaStream } | null = null;

// The person's own microphone track while they are in a call and the microphone is on: the one local audio track,
// never the screen share's, a peer's or an agent's.
function liveMicrophone(): MediaStreamTrack | null {
    if (!state.inCall || !state.audioEnabled) return null;
    return state.localStream?.getAudioTracks()[0] ?? null;
}

/** Tells every handler of the microphone's new state; a call that changes nothing for the handlers tells nothing. */
export function microphoneChanged() {
    const track = liveMicrophone();
    if (track === (handed?.track ?? null)) return;
    handed = track === null ? null : { track, stream: new MediaStream([track]) };
    for (const handler of handlers) handler(handed?.stream ?? null);
}

/** Registers a handler and tells it the current state at once. */
export function onMicrophone(handler: MicrophoneHandler) {
    microphoneChanged();
    handlers.push(handler);
    handler(handed?.stream ?? null);
}
