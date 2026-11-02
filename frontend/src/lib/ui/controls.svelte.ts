import { CameraError, addCamera, releaseCamera, toggleAudio, toggleScreenShare } from "#lib/call/media.ts";
import { isConnected, send } from "#lib/call/signaling.ts";
import { state } from "#lib/call/state.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { setStatus } from "#lib/ui/status.svelte.ts";
import { TOAST_KIND, showToast } from "#lib/ui/toast.svelte.ts";

const CAMERA_ERROR_STATUS_MS = 2000;
const LOCK_LINE = Object.freeze({ LOCKED: "No one new can join", UNLOCKED: "New people can join" });
const LOCK_TOAST = Object.freeze({ LOCKED: "Call locked", UNLOCKED: "Call unlocked" });

export const deviceButtons = $state({ hasMedia: true, isMicrophoneOn: true, isCameraOn: true, isSharing: false });
export const lockSwitch = $state({ isShown: false, isLocked: false, line: "" });

let onLeaveRequested: (() => void) | null = null;

export function installLeaveRequested(callback: () => void) {
    if (onLeaveRequested) throw new Error("The leave request is already installed");
    onLeaveRequested = callback;
}

export function requestLeave() {
    if (!onLeaveRequested) throw new Error("The leave request is not installed");
    onLeaveRequested();
}

/** Put `items` into More, below Lock call and above the legal links. */
export function addMoreItems(...items: Element[]) {
    const legalRow = document.querySelector("#moreMenu nav");
    if (!legalRow) throw new Error("More has no legal row");
    legalRow.before(...items);
}

export function showLockButton(isLocked: boolean) {
    lockSwitch.isShown = true;
    lockSwitch.isLocked = isLocked;
    lockSwitch.line = isLocked ? LOCK_LINE.LOCKED : LOCK_LINE.UNLOCKED;
}

export function hideLockButton() {
    lockSwitch.isShown = false;
}

export function confirmLockChange(isLocked: boolean) {
    if (!state.isHost) return;
    showToast(isLocked ? LOCK_TOAST.LOCKED : LOCK_TOAST.UNLOCKED, TOAST_KIND.DONE);
}

export function renderControls() {
    deviceButtons.hasMedia = Boolean(state.localStream);
    deviceButtons.isMicrophoneOn = state.audioEnabled;
    deviceButtons.isCameraOn = state.videoEnabled;
    deviceButtons.isSharing = Boolean(state.screenStream);
}

export function muteClicked() {
    if (!state.localStream) return;
    toggleAudio();
    renderControls();
}

function showCameraError(error: Error) {
    setStatus(`No camera found (${error.name})`, "error");
    setTimeout(() => {
        if (state.roomId) setStatus("In call", "connected");
    }, CAMERA_ERROR_STATUS_MS);
}

export async function cameraClicked() {
    if (!state.localStream) return;
    if (state.videoEnabled) {
        releaseCamera();
    } else {
        try {
            await addCamera();
        } catch (error) {
            showCameraError(error as Error);
            return;
        }
    }
    renderControls();
}

export function lockClicked() {
    if (!isConnected() || !state.isHost) return;
    send({ type: state.isLocked ? SIGNAL_TYPE.UNLOCK_ROOM : SIGNAL_TYPE.LOCK_ROOM });
}

export async function screenClicked() {
    if (!state.localStream) return;
    try {
        await toggleScreenShare(() => void screenClicked());
    } catch (error) {
        if (error instanceof CameraError) {
            showCameraError(error);
        } else {
            setStatus(`Screen share failed: ${(error as Error).message}`, "error");
        }
    }
    renderControls();
}
