import { CameraError, addCamera, releaseCamera, toggleAudio, toggleScreenShare } from "./media.js";
import { SIGNAL_TYPE, isConnected, send } from "./signaling.js";
import { closeOpenMenu, registerMenu } from "./menus.js";
import { state } from "./state.js";
import { setStatus } from "./status.js";
import { TOAST_KIND, showToast } from "./toast.js";

const CAMERA_ERROR_STATUS_MS = 2000;
const LOCK_LINE = Object.freeze({ LOCKED: "No one new can join", UNLOCKED: "New people can join" });
const LOCK_TOAST = Object.freeze({ LOCKED: "Call locked", UNLOCKED: "Call unlocked" });

const muteButton = document.getElementById("muteBtn");
const videoButton = document.getElementById("videoBtn");
const screenButton = document.getElementById("screenBtn");
const overlayScrim = document.getElementById("overlayScrim");
const MENUS = [
    {
        button: document.getElementById("moreBtn"),
        dialog: document.getElementById("moreMenu"),
        firstFocus: ".mi",
        scrim: overlayScrim,
    },
    {
        button: document.getElementById("inviteBtn"),
        dialog: document.getElementById("inviteMenu"),
        firstFocus: ".invite-copy",
        scrim: overlayScrim,
    },
];
const lockButton = document.getElementById("lockBtn");
const leaveButton = document.getElementById("leaveBtn");

export function renderDeviceToggle(button, isOn) {
    button.classList.toggle("off", !isOn);
    button.setAttribute("aria-pressed", String(!isOn));
}

export function showLockButton(isLocked) {
    lockButton.classList.remove("hidden");
    lockButton.setAttribute("aria-checked", String(isLocked));
    lockButton.querySelector("small").textContent = isLocked ? LOCK_LINE.LOCKED : LOCK_LINE.UNLOCKED;
    lockButton.querySelector(".switch").classList.toggle("on", isLocked);
}

export function hideLockButton() {
    lockButton.classList.add("hidden");
}

export function confirmLockChange(isLocked) {
    if (!state.isHost) return;
    showToast(isLocked ? LOCK_TOAST.LOCKED : LOCK_TOAST.UNLOCKED, TOAST_KIND.DONE);
}

export function renderControls() {
    const hasMedia = Boolean(state.localStream);
    renderDeviceToggle(muteButton, hasMedia && state.audioEnabled);
    renderDeviceToggle(videoButton, hasMedia && state.videoEnabled);
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

function showCameraError(error) {
    setStatus(`No camera found (${error.name})`, "error");
    setTimeout(() => {
        if (state.roomId) setStatus("In call", "connected");
    }, CAMERA_ERROR_STATUS_MS);
}

async function handleVideoClick() {
    if (!state.localStream) return;
    if (state.videoEnabled) {
        releaseCamera();
    } else {
        try {
            await addCamera();
        } catch (error) {
            showCameraError(error);
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
    if (!state.localStream) return;
    try {
        await toggleScreenShare(() => screenButton.click());
    } catch (error) {
        if (error instanceof CameraError) {
            showCameraError(error);
        } else {
            setStatus(`Screen share failed: ${error.message}`, "error");
        }
    }
    renderControls();
}

export function initControls({ onLeaveRequested }) {
    muteButton.addEventListener("click", handleMuteClick);
    videoButton.addEventListener("click", () => {
        void handleVideoClick();
    });
    screenButton.addEventListener("click", () => {
        void handleScreenClick();
    });
    for (const menu of MENUS) registerMenu(menu);
    document.getElementById("inviteCloseBtn").addEventListener("click", closeOpenMenu);
    lockButton.addEventListener("click", handleLockClick);
    leaveButton.addEventListener("click", onLeaveRequested);
}
