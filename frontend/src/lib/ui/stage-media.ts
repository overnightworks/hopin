import {
    ownPicture,
    refreshPictureReady,
    registerRemoteVideo,
    remoteQualityOf,
    remoteStreamOf,
    remoteVideoOf,
} from "#lib/ui/stage.svelte.ts";

// Svelte runs `update` when the delivery changes, never at mount: the own videos mount before any stream arrives.
// The delivery is the action's argument only so that Svelte calls `update` when it changes.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function ownPictureMedia(video: HTMLVideoElement, _delivery: number) {
    return {
        update() {
            video.srcObject = ownPicture.stream;
        },
    };
}

// Strings and numbers only: Svelte deep-reads an action's argument, and the delivery is what makes it call `update`.
export interface RemoteDelivery {
    participantId: string;
    delivery: number;
}

const tracksWatched = new WeakSet<MediaStreamTrack>();

// A media element does not start playing while the stream holds a video track that
// has received no frame yet, which silences the person's voice until the camera is on.
// So the element gets the audio, and each video track joins when its first frame arrives.
function attachTracksThatHaveData(video: HTMLVideoElement, stream: MediaStream) {
    const attached = (video.srcObject as MediaStream | null)?.getTracks() ?? [];
    const wanted = stream.getTracks().filter((track) => track.kind === "audio" || !track.muted);
    const unchanged = wanted.length === attached.length && wanted.every((track) => attached.includes(track));
    if (!unchanged) video.srcObject = new MediaStream(wanted);
}

function watchVideoTrack(participantId: string, stream: MediaStream, track: MediaStreamTrack) {
    if (tracksWatched.has(track)) return;
    tracksWatched.add(track);
    track.addEventListener("unmute", () => {
        const video = remoteVideoOf(participantId);
        if (video) attachTracksThatHaveData(video, stream);
        refreshPictureReady(participantId);
    });
    track.addEventListener("mute", () => {
        refreshPictureReady(participantId);
    });
}

function attachRemoteStream(video: HTMLVideoElement, participantId: string) {
    const stream = remoteStreamOf(participantId);
    if (!stream) return;
    attachTracksThatHaveData(video, stream);
    for (const track of stream.getVideoTracks()) watchVideoTrack(participantId, stream, track);
    refreshPictureReady(participantId);
}

// Svelte never calls `update` for the first value, so the mount attaches a stream that arrived before the video.
// `update` reads plain maps only, so another tile's add or remove does not run it.
export function remoteMedia(video: HTMLVideoElement, { participantId }: RemoteDelivery) {
    const unregister = registerRemoteVideo(participantId, video);
    attachRemoteStream(video, participantId);
    return {
        update() {
            attachRemoteStream(video, participantId);
        },
        destroy: unregister,
    };
}

// Written on every report, so a repaint follows even when the quality is the same as before.
export function qualityPill(indicator: HTMLElement, { participantId }: RemoteDelivery) {
    const paint = () => {
        const quality = remoteQualityOf(participantId);
        if (quality) indicator.dataset.quality = quality;
    };
    paint();
    return { update: paint };
}
