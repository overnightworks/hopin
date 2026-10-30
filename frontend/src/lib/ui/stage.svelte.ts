import { state } from "#lib/call/state.ts";

// The starting values are what the layout's markup shows before the first call.
export const ownPicture = $state<{
    stream: MediaStream | null;
    delivery: number;
    mirrored: boolean;
    showsVideo: boolean;
    muted: boolean;
}>({ stream: null, delivery: 0, mirrored: true, showsVideo: true, muted: false });

// The call state is read when these run, not reactively: the mirror follows a screen share only at the next stream.
export function showLocalStream(stream: MediaStream | null) {
    ownPicture.stream = stream;
    // The same stream arrives again after a track swap; the counter is what makes the video take it again.
    ownPicture.delivery += 1;
    ownPicture.mirrored = !state.screenStream;
}

export function updateLocalPreview() {
    ownPicture.showsVideo = state.videoEnabled;
    ownPicture.muted = !state.audioEnabled;
}
