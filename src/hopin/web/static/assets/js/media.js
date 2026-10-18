import { addTrackToPeers, removeTrackFromPeers, replaceVideoTrackOnPeers } from "./mesh.js";
import { showLocalStream } from "./stage.js";
import { state } from "./state.js";

function stopTracks(stream) {
    for (const track of stream.getTracks()) {
        track.stop();
    }
}

function replaceVideoTrack(newTrack) {
    const oldTrack = state.localStream.getVideoTracks()[0];
    if (oldTrack) {
        state.localStream.removeTrack(oldTrack);
    }
    state.localStream.addTrack(newTrack);
    showLocalStream(state.localStream);
    replaceVideoTrackOnPeers(newTrack);
}

export async function acquireMedia(preferCamera) {
    if (preferCamera) {
        try {
            state.localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
            state.hasCamera = true;
            state.videoEnabled = true;
            return;
        } catch {
            // camera unavailable; fall back to the audio-only attempt below
        }
    }

    try {
        state.localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        state.hasCamera = false;
        state.videoEnabled = false;
    } catch (error) {
        // error.name distinguishes a permission denial (NotAllowedError) from an
        // absent device (NotFoundError), which call for different advice.
        throw new Error(
            `No camera or microphone available (${error.name}). Please connect a device or grant permission and try again.`,
        );
    }
}

export function toggleAudio() {
    state.audioEnabled = !state.audioEnabled;
    for (const track of state.localStream.getAudioTracks()) {
        track.enabled = state.audioEnabled;
    }
}

export function toggleVideo() {
    state.videoEnabled = !state.videoEnabled;
    for (const track of state.localStream.getVideoTracks()) {
        track.enabled = state.videoEnabled;
    }
}

export async function addCamera() {
    const cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
    const cameraTrack = cameraStream.getVideoTracks()[0];
    state.localStream.addTrack(cameraTrack);
    addTrackToPeers(cameraTrack, state.localStream);
    state.hasCamera = true;
    state.videoEnabled = true;
    showLocalStream(state.localStream);
}

async function stopScreenShare() {
    const screenAudioTrack = state.screenStream.getAudioTracks()[0];
    if (screenAudioTrack) {
        removeTrackFromPeers(screenAudioTrack);
    }
    stopTracks(state.screenStream);
    state.screenStream = null;
    const cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
    replaceVideoTrack(cameraStream.getVideoTracks()[0]);
    showLocalStream(state.localStream);
}

async function startScreenShare(onEnded) {
    state.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
    const screenTrack = state.screenStream.getVideoTracks()[0];
    replaceVideoTrack(screenTrack);
    const screenAudioTrack = state.screenStream.getAudioTracks()[0];
    if (screenAudioTrack) {
        addTrackToPeers(screenAudioTrack, state.screenStream);
    }
    screenTrack.onended = onEnded;
}

export async function toggleScreenShare(onEnded) {
    if (state.screenStream) {
        await stopScreenShare();
    } else {
        await startScreenShare(onEnded);
    }
}

export function stopLocalMedia() {
    if (state.localStream) {
        stopTracks(state.localStream);
        state.localStream = null;
    }
    if (state.screenStream) {
        stopTracks(state.screenStream);
        state.screenStream = null;
    }
}
