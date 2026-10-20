import { addCamera, toggleAudio, toggleScreenShare, toggleVideo } from "./media.js";
import { SIGNAL_TYPE, isConnected, send } from "./signaling.js";
import { state } from "./state.js";
import { setStatus } from "./status.js";

const CAMERA_ERROR_STATUS_MS = 2000;

const muteButton = document.getElementById("muteBtn");
const videoButton = document.getElementById("videoBtn");
const screenButton = document.getElementById("screenBtn");
const lockButton = document.getElementById("lockBtn");
const leaveButton = document.getElementById("leaveBtn");

export function renderDeviceToggle(button, isOn) {
    button.classList.toggle("off", !isOn);
    button.setAttribute("aria-pressed", String(!isOn));
}

export function showLockButton(isLocked) {
    lockButton.classList.remove("hidden");
    lockButton.classList.toggle("active", isLocked);
    lockButton.setAttribute("aria-pressed", String(isLocked));
}

export function hideLockButton() {
    lockButton.classList.add("hidden");
}

export function renderControls() {
    const hasMedia = Boolean(state.localStream);
    renderDeviceToggle(muteButton, hasMedia && state.audioEnabled);
    renderDeviceToggle(videoButton, hasMedia && state.hasCamera && state.videoEnabled);
    const isSharing = Boolean(state.screenStream);
    screenButton.classList.toggle("active", isSharing);
    screenButton.setAttribute("aria-pressed", String(isSharing));
    for (const button of [muteButton, videoButton, screenButton]) button.disabled = !hasMedia;
}

function handleMuteClick() {
    if (!state.localStream) return;
    toggleAudio();
    renderControls();
}

async function handleVideoClick() {
    if (!state.localStream) return;
    if (state.hasCamera) {
        toggleVideo();
    } else {
        try {
            await addCamera();
        } catch (error) {
            setStatus(`No camera found (${error.name})`, "error");
            setTimeout(() => {
                if (state.roomId) setStatus("In call", "connected");
            }, CAMERA_ERROR_STATUS_MS);
            return;
        }
    }
    renderControls();
}

function handleLockClick() {
    if (!isConnected() || !state.isHost) return;
    send({ type: state.isLocked ? SIGNAL_TYPE.UNLOCK_ROOM : SIGNAL_TYPE.LOCK_ROOM });
}

async function handleScreenClick() {
    if (!state.localStream || !state.hasCamera) return;
    try {
        await toggleScreenShare(() => screenButton.click());
        renderControls();
    } catch (error) {
        setStatus(`Screen share failed: ${error.message}`, "error");
    }
}

export function initControls({ onLeaveRequested }) {
    muteButton.addEventListener("click", handleMuteClick);
    videoButton.addEventListener("click", () => {
        void handleVideoClick();
    });
    screenButton.addEventListener("click", () => {
        void handleScreenClick();
    });
    lockButton.addEventListener("click", handleLockClick);
    leaveButton.addEventListener("click", onLeaveRequested);
}
