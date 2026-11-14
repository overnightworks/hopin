import type { PictureBounds } from "#lib/call/feature-port.ts";
import { state } from "#lib/call/state.ts";

const JPEG_QUALITIES = [0.85, 0.7, 0.5, 0.3];
const DATA_URL_PREFIX = "data:image/jpeg;base64,";
const BYTES_PER_BASE64_GROUP = 3;
const CHARACTERS_PER_BASE64_GROUP = 4;

// The person's own camera track while they are in a call with the camera on: never the screen share's, which replaces
// the camera's track in the local stream while it runs.
function liveCamera(): MediaStreamTrack | null {
    if (!state.inCall || !state.videoEnabled || state.screenStream !== null) return null;
    const track = state.localStream?.getVideoTracks()[0];
    return track !== undefined && track.enabled && track.readyState === "live" ? track : null;
}

function hasFrame(video: HTMLVideoElement): boolean {
    return video.videoWidth > 0 && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA;
}

function firstFrame(video: HTMLVideoElement): Promise<void> {
    if (hasFrame(video)) return Promise.resolve();
    return new Promise((resolve) => {
        video.addEventListener("loadeddata", () => {
            resolve();
        });
    });
}

function encodedBytes(base64: string): number {
    return (base64.length / CHARACTERS_PER_BASE64_GROUP) * BYTES_PER_BASE64_GROUP;
}

function encode(canvas: HTMLCanvasElement, maxBytes: number): string {
    let base64 = "";
    for (const quality of JPEG_QUALITIES) {
        base64 = canvas.toDataURL("image/jpeg", quality).slice(DATA_URL_PREFIX.length);
        if (encodedBytes(base64) <= maxBytes) break;
    }
    return base64;
}

/** Takes one still picture of the camera: a frame drawn on a canvas, scaled so its longer side is at most `maxPixels`, and encoded as JPEG at the best quality that stays within `maxBytes`; the last quality's picture when none does. Nothing keeps the frame, the canvas or the playing element. */
export async function takePicture(bounds: PictureBounds): Promise<string | null> {
    const track = liveCamera();
    if (track === null) return null;
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.srcObject = new MediaStream([track]);
    try {
        await video.play();
        await firstFrame(video);
        const scale = Math.min(1, bounds.maxPixels / Math.max(video.videoWidth, video.videoHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(video.videoWidth * scale);
        canvas.height = Math.round(video.videoHeight * scale);
        const context = canvas.getContext("2d");
        if (context === null) return null;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        return encode(canvas, bounds.maxBytes);
    } finally {
        video.pause();
        video.srcObject = null;
    }
}
