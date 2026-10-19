import { addCamera, toggleAudio, toggleScreenShare, toggleVideo } from "./media.js";
import { SIGNAL_TYPE, isConnected, send } from "./signaling.js";
import { state } from "./state.js";
import { setStatus } from "./status.js";

const COPIED_LABEL_MS = 2000;
const CAMERA_ERROR_STATUS_MS = 2000;

const shareBox = document.getElementById("shareBox");
const shareUrl = document.getElementById("shareUrl");
const copyButton = document.getElementById("copyBtn");
const muteButton = document.getElementById("muteBtn");
const videoButton = document.getElementById("videoBtn");
const screenButton = document.getElementById("screenBtn");
const lockButton = document.getElementById("lockBtn");
const leaveButton = document.getElementById("leaveBtn");

function flashCopiedLabel() {
    copyButton.textContent = "Copied!";
    setTimeout(() => {
        copyButton.textContent = "Copy";
    }, COPIED_LABEL_MS);
}

export function showShareLink(url) {
    shareUrl.value = url;
    shareBox.classList.add("visible");
    navigator.clipboard
        .writeText(url)
        .then(flashCopiedLabel)
        .catch(() => {
            // clipboard access can be denied; the share box still shows the link to copy by hand
        });
}

export function hideShareBox() {
    shareBox.classList.remove("visible");
}

export function showLockButton(isLocked) {
    lockButton.textContent = isLocked ? "Unlock Room" : "Lock Room";
    lockButton.className = isLocked ? "btn-warning" : "btn-secondary";
}

export function hideLockButton() {
    lockButton.classList.add("hidden");
}

export function renderVideoButton() {
    videoButton.textContent = state.videoEnabled ? "Cam On" : "Cam Off";
    videoButton.className = state.videoEnabled ? "btn-active" : "btn-muted";
}

function renderMuteButton() {
    muteButton.textContent = state.audioEnabled ? "Mic On" : "Mic Off";
    muteButton.className = state.audioEnabled ? "btn-active" : "btn-muted";
}

function renderScreenButton() {
    screenButton.textContent = state.screenStream ? "Stop Sharing" : "Share Screen";
    screenButton.className = state.screenStream ? "btn-muted" : "btn-secondary";
}

function handleMuteClick() {
    if (!state.localStream) return;
    toggleAudio();
    renderMuteButton();
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
    renderVideoButton();
}

function handleLockClick() {
    if (!isConnected() || !state.isHost) return;
    send({ type: state.isLocked ? SIGNAL_TYPE.UNLOCK_ROOM : SIGNAL_TYPE.LOCK_ROOM });
}

async function handleScreenClick() {
    if (!state.localStream || !state.hasCamera) return;
    try {
        await toggleScreenShare(() => screenButton.click());
        renderScreenButton();
    } catch (error) {
        setStatus(`Screen share failed: ${error.message}`, "error");
    }
}

function handleCopyClick() {
    shareUrl.select();
    void navigator.clipboard.writeText(shareUrl.value);
    flashCopiedLabel();
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
    copyButton.addEventListener("click", handleCopyClick);
    leaveButton.addEventListener("click", onLeaveRequested);
}
