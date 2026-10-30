import { addTrackToPeers, removeTrackFromPeers, replaceVideoTrackOnPeers } from "#lib/call/mesh.ts";
import { announceMediaState } from "#lib/call/media-state.ts";
import { showLocalStream, updateLocalPreview } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";

let mediaSession = 0;
let cameraRequest = 0;
let screenShareChange: Promise<void> | null = null;

function stopTracks(stream: MediaStream) {
    for (const track of stream.getTracks()) {
        track.stop();
    }
}

export class CameraError extends Error {
    constructor(cause: Error) {
        super(cause.message, { cause });
        this.name = cause.name;
    }
}

// A permission prompt or the screen picker can stay open past the end of the call.
// stopLocalMedia ends the session, so a stream that arrives after it, or a request
// that would start after it, belongs to no call: it is stopped and changes nothing.
// A camera request also stops when a newer press, a share or the end of the call
// overtook it (isCurrent).
async function acquireStream(
    session: number,
    request: () => Promise<MediaStream>,
    adopt: (stream: MediaStream) => void,
    isCurrent: () => boolean = () => true,
): Promise<void> {
    if (session !== mediaSession) return;
    const stream = await request();
    if (session !== mediaSession || !isCurrent()) {
        stopTracks(stream);
        return;
    }
    adopt(stream);
}

function adoptLocalStream(stream: MediaStream, cameraOn: boolean) {
    state.localStream = stream;
    state.videoEnabled = cameraOn;
}

// Every caller guarantees the stream: the controls and the pre-join act only on a started one.
function requireLocalStream(): MediaStream {
    if (!state.localStream) throw new Error("No local stream");
    return state.localStream;
}

// The browser resolves a camera or screen request only with a video track.
function videoTrackOf(stream: MediaStream): MediaStreamTrack {
    const track = stream.getVideoTracks()[0];
    if (!track) throw new Error("The stream carries no video track");
    return track;
}

function dropVideoTrack() {
    const localStream = requireLocalStream();
    const oldTrack = localStream.getVideoTracks()[0];
    if (oldTrack) {
        localStream.removeTrack(oldTrack);
        oldTrack.stop();
    }
}

function replaceVideoTrack(newTrack: MediaStreamTrack) {
    dropVideoTrack();
    const localStream = requireLocalStream();
    localStream.addTrack(newTrack);
    showLocalStream(localStream);
    void replaceVideoTrackOnPeers(newTrack);
}

export async function acquireMedia(): Promise<void> {
    const session = mediaSession;
    try {
        await acquireStream(
            session,
            () => navigator.mediaDevices.getUserMedia({ video: true, audio: true }),
            (stream) => {
                adoptLocalStream(stream, true);
            },
        );
        return;
    } catch {
        // camera unavailable; fall back to the audio-only attempt below
    }

    try {
        await acquireStream(
            session,
            () => navigator.mediaDevices.getUserMedia({ audio: true }),
            (stream) => {
                adoptLocalStream(stream, false);
            },
        );
    } catch (error) {
        // The rejection's name distinguishes a permission denial (NotAllowedError) from an
        // absent device (NotFoundError), which call for different advice.
        throw new Error(
            `No camera or microphone available (${(error as Error).name}). Please connect a device or grant permission and try again.`,
            { cause: error },
        );
    }
}

function localMediaChanged() {
    updateLocalPreview();
    announceMediaState();
}

export function toggleAudio() {
    const localStream = requireLocalStream();
    state.audioEnabled = !state.audioEnabled;
    for (const track of localStream.getAudioTracks()) {
        track.enabled = state.audioEnabled;
    }
    localMediaChanged();
}

async function openCamera(adopt: (cameraTrack: MediaStreamTrack) => void): Promise<void> {
    cameraRequest += 1;
    const ticket = cameraRequest;
    const isCurrent = () => ticket === cameraRequest;
    try {
        await acquireStream(
            mediaSession,
            () => navigator.mediaDevices.getUserMedia({ video: true }),
            (cameraStream) => {
                adopt(videoTrackOf(cameraStream));
            },
            isCurrent,
        );
    } catch (error) {
        if (isCurrent()) throw new CameraError(error as Error);
    }
}

function sendNoVideo() {
    dropVideoTrack();
    void replaceVideoTrackOnPeers(null);
}

// While a share runs the choice is only recorded; the share's end applies it.
export async function addCamera(): Promise<void> {
    if (state.screenStream) {
        state.videoEnabled = true;
        localMediaChanged();
        return;
    }
    await openCamera((cameraTrack) => {
        replaceVideoTrack(cameraTrack);
        state.videoEnabled = true;
        localMediaChanged();
    });
}

// Stopping the track, not only disabling it, turns the camera's light off.
export function releaseCamera() {
    cameraRequest += 1;
    state.videoEnabled = false;
    if (!state.screenStream) sendNoVideo();
    localMediaChanged();
}

async function stopScreenShare(screenStream: MediaStream) {
    const screenAudioTrack = screenStream.getAudioTracks()[0];
    if (screenAudioTrack) {
        removeTrackFromPeers(screenAudioTrack);
    }
    stopTracks(screenStream);
    state.screenStream = null;
    if (!state.videoEnabled) {
        sendNoVideo();
        return;
    }
    try {
        await openCamera(replaceVideoTrack);
    } catch (error) {
        state.videoEnabled = false;
        sendNoVideo();
        localMediaChanged();
        throw error;
    }
}

async function startScreenShare(onEnded: () => void) {
    await acquireStream(
        mediaSession,
        () => navigator.mediaDevices.getDisplayMedia({ video: true, audio: true }),
        (screenStream) => {
            const screenTrack = videoTrackOf(screenStream);
            cameraRequest += 1;
            state.screenStream = screenStream;
            replaceVideoTrack(screenTrack);
            const screenAudioTrack = screenStream.getAudioTracks()[0];
            if (screenAudioTrack) {
                addTrackToPeers(screenAudioTrack, screenStream);
            }
            screenTrack.onended = onEnded;
        },
    );
}

async function changeScreenShare(onEnded: () => void) {
    if (state.screenStream) {
        await stopScreenShare(state.screenStream);
    } else {
        await startScreenShare(onEnded);
    }
}

// The screen picker stays open while a change is pending. A second press joins that
// change instead of opening a second picker, whose stream would replace the first
// one in state and leave the first one's audio capturing with nothing to stop it.
export function toggleScreenShare(onEnded: () => void): Promise<void> {
    if (!screenShareChange) {
        screenShareChange = changeScreenShare(onEnded)
            .then(localMediaChanged)
            .finally(() => {
                screenShareChange = null;
            });
    }
    return screenShareChange;
}

export function stopLocalMedia() {
    mediaSession += 1;
    cameraRequest += 1;
    if (state.localStream) {
        stopTracks(state.localStream);
        state.localStream = null;
    }
    if (state.screenStream) {
        stopTracks(state.screenStream);
        state.screenStream = null;
    }
}
