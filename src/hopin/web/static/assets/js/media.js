import { addTrackToPeers, removeTrackFromPeers, replaceVideoTrackOnPeers } from "./mesh.js";
import { announceMediaState } from "./media-state.js";
import { showLocalStream, updateLocalPreview } from "./stage.js";
import { state } from "./state.js";

let mediaSession = 0;
let cameraRequest = 0;
let screenShareChange = null;

function stopTracks(stream) {
    for (const track of stream.getTracks()) {
        track.stop();
    }
}

export class CameraError extends Error {
    constructor(cause) {
        super(cause.message, { cause });
        this.name = cause.name;
    }
}

// A permission prompt or the screen picker can stay open past the end of the call.
// stopLocalMedia ends the session, so a stream that arrives after it, or a request
// that would start after it, belongs to no call: it is stopped and changes nothing.
// A camera request also stops when a newer press, a share or the end of the call
// overtook it (isCurrent).
async function acquireStream(session, request, adopt, isCurrent = () => true) {
    if (session !== mediaSession) return;
    const stream = await request();
    if (session !== mediaSession || !isCurrent()) {
        stopTracks(stream);
        return;
    }
    adopt(stream);
}

function adoptLocalStream(stream, cameraOn) {
    state.localStream = stream;
    state.videoEnabled = cameraOn;
}

function dropVideoTrack() {
    const oldTrack = state.localStream.getVideoTracks()[0];
    if (oldTrack) {
        state.localStream.removeTrack(oldTrack);
        oldTrack.stop();
    }
}

function replaceVideoTrack(newTrack) {
    dropVideoTrack();
    state.localStream.addTrack(newTrack);
    showLocalStream(state.localStream);
    void replaceVideoTrackOnPeers(newTrack);
}

export async function acquireMedia(preferCamera) {
    const session = mediaSession;
    if (preferCamera) {
        try {
            await acquireStream(
                session,
                () => navigator.mediaDevices.getUserMedia({ video: true, audio: true }),
                (stream) => adoptLocalStream(stream, true),
            );
            return;
        } catch {
            // camera unavailable; fall back to the audio-only attempt below
        }
    }

    try {
        await acquireStream(
            session,
            () => navigator.mediaDevices.getUserMedia({ audio: true }),
            (stream) => adoptLocalStream(stream, false),
        );
    } catch (error) {
        // error.name distinguishes a permission denial (NotAllowedError) from an
        // absent device (NotFoundError), which call for different advice.
        throw new Error(
            `No camera or microphone available (${error.name}). Please connect a device or grant permission and try again.`,
        );
    }
}

function localMediaChanged() {
    updateLocalPreview();
    announceMediaState();
}

export function toggleAudio() {
    state.audioEnabled = !state.audioEnabled;
    for (const track of state.localStream.getAudioTracks()) {
        track.enabled = state.audioEnabled;
    }
    localMediaChanged();
}

async function openCamera(adopt) {
    cameraRequest += 1;
    const ticket = cameraRequest;
    const isCurrent = () => ticket === cameraRequest;
    try {
        await acquireStream(
            mediaSession,
            () => navigator.mediaDevices.getUserMedia({ video: true }),
            (cameraStream) => adopt(cameraStream.getVideoTracks()[0]),
            isCurrent,
        );
    } catch (error) {
        if (isCurrent()) throw new CameraError(error);
    }
}

function sendNoVideo() {
    dropVideoTrack();
    void replaceVideoTrackOnPeers(null);
}

// While a share runs the choice is only recorded; the share's end applies it.
export async function addCamera() {
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

async function stopScreenShare() {
    const screenAudioTrack = state.screenStream.getAudioTracks()[0];
    if (screenAudioTrack) {
        removeTrackFromPeers(screenAudioTrack);
    }
    stopTracks(state.screenStream);
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

async function startScreenShare(onEnded) {
    await acquireStream(
        mediaSession,
        () => navigator.mediaDevices.getDisplayMedia({ video: true, audio: true }),
        (screenStream) => {
            cameraRequest += 1;
            state.screenStream = screenStream;
            const screenTrack = screenStream.getVideoTracks()[0];
            replaceVideoTrack(screenTrack);
            const screenAudioTrack = screenStream.getAudioTracks()[0];
            if (screenAudioTrack) {
                addTrackToPeers(screenAudioTrack, screenStream);
            }
            screenTrack.onended = onEnded;
        },
    );
}

async function changeScreenShare(onEnded) {
    if (state.screenStream) {
        await stopScreenShare();
    } else {
        await startScreenShare(onEnded);
    }
}

// The screen picker stays open while a change is pending. A second press joins that
// change instead of opening a second picker, whose stream would replace the first
// one in state and leave the first one's audio capturing with nothing to stop it.
export function toggleScreenShare(onEnded) {
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
