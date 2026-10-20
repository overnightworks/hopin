import { acquireMedia, addCamera, releaseCamera, toggleAudio } from "./media.js";
import { SCREEN, goHome, showScreen } from "./router.js";
import { showLocalStream, updateLocalPreview } from "./stage.js";
import { state } from "./state.js";

const MICROPHONE_LABEL = { ON: "Mute", OFF: "Unmute" };
const START_LABEL = {
    CAMERA_AND_MIC: "Start with camera and mic",
    CAMERA_ONLY: "Start with camera only",
    MIC_ONLY: "Start with mic only",
    NEITHER: "Start with camera and mic off",
};

const microphoneButton = document.getElementById("prejoinMicBtn");
const cameraButton = document.getElementById("prejoinCamBtn");
const startButton = document.getElementById("prejoinStartBtn");
const message = document.getElementById("prejoinMessage");
const wayHomeButtons = [document.getElementById("prejoinHomeBtn"), document.getElementById("prejoinBackBtn")];

let mediaRequestPending = false;

function startLabel(cameraOn, microphoneOn) {
    if (cameraOn && microphoneOn) return START_LABEL.CAMERA_AND_MIC;
    if (cameraOn) return START_LABEL.CAMERA_ONLY;
    if (microphoneOn) return START_LABEL.MIC_ONLY;
    return START_LABEL.NEITHER;
}

function renderToggle(button, isOn) {
    button.classList.toggle("off", !isOn);
    button.setAttribute("aria-pressed", String(!isOn));
}

function render() {
    const cameraOn = state.videoEnabled;
    const microphoneOn = state.audioEnabled;
    renderToggle(microphoneButton, microphoneOn);
    microphoneButton.setAttribute("aria-label", microphoneOn ? MICROPHONE_LABEL.ON : MICROPHONE_LABEL.OFF);
    renderToggle(cameraButton, cameraOn);
    startButton.textContent = startLabel(cameraOn, microphoneOn);
    const nothingToUse = mediaRequestPending || !state.localStream;
    for (const control of [microphoneButton, cameraButton, startButton]) {
        control.disabled = nothingToUse;
    }
}

// A press while a request is pending would act on media that is not there yet,
// so every control waits for the answer.
async function whileMediaIsRequested(request) {
    mediaRequestPending = true;
    render();
    try {
        await request();
    } finally {
        mediaRequestPending = false;
        render();
    }
}

async function openMedia() {
    await acquireMedia(true);
    showLocalStream(state.localStream);
    updateLocalPreview();
}

function showMediaFailure(failure) {
    message.textContent = failure.message;
    message.classList.remove("hidden");
}

function toggleMicrophone() {
    toggleAudio();
    render();
}

async function toggleCamera() {
    if (state.videoEnabled) {
        releaseCamera();
        render();
        return;
    }
    try {
        await whileMediaIsRequested(addCamera);
    } catch {
        // No camera answered: the camera stays off, as the button and the preview already show.
    }
}

export function openPrejoin() {
    showScreen(SCREEN.PREJOIN);
    whileMediaIsRequested(openMedia).catch(showMediaFailure);
}

export function initPrejoin({ onStart }) {
    microphoneButton.addEventListener("click", toggleMicrophone);
    cameraButton.addEventListener("click", () => {
        void toggleCamera();
    });
    startButton.addEventListener("click", onStart);
    for (const button of wayHomeButtons) {
        button.addEventListener("click", goHome);
    }
}
