import { renderDeviceToggle } from "./controls.js";
import { acquireMedia, addCamera, releaseCamera, toggleAudio } from "./media.js";
import { goHome } from "#lib/ui/navigation.ts";
import { SCREEN, showScreen } from "#lib/ui/screens.svelte.ts";
import { showLocalStream, updateLocalPreview } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";

export const PREJOIN_KIND = Object.freeze({ HOST: "host", GUEST: "guest" });

const KIND_TEXTS = {
    [PREJOIN_KIND.HOST]: {
        title: "New call",
        heading: "Ready to start?",
        subLine: "You'll get a link to invite people.",
        buttonLabels: {
            CAMERA_AND_MIC: "Start with camera and mic",
            CAMERA_ONLY: "Start with camera only",
            MIC_ONLY: "Start with mic only",
            NEITHER: "Start with camera and mic off",
        },
    },
    [PREJOIN_KIND.GUEST]: {
        title: "Join call",
        heading: "Ready to join?",
        subLine: "",
        buttonLabels: {
            CAMERA_AND_MIC: "Join with camera and mic",
            CAMERA_ONLY: "Join with camera only",
            MIC_ONLY: "Join with mic only",
            NEITHER: "Join with camera and mic off",
        },
    },
};

const microphoneButton = document.getElementById("prejoinMicBtn");
const cameraButton = document.getElementById("prejoinCamBtn");
const startButton = document.getElementById("prejoinStartBtn");
const titleElement = document.getElementById("prejoinTitle");
const headingElement = document.getElementById("prejoinHeading");
const subLineElement = document.getElementById("prejoinSub");
const message = document.getElementById("prejoinMessage");
const wayHomeButtons = [document.getElementById("prejoinHomeBtn"), document.getElementById("prejoinBackBtn")];

let mediaRequestPending = false;
let openKind = PREJOIN_KIND.HOST;
let texts = KIND_TEXTS[openKind];

function startLabel(cameraOn, microphoneOn) {
    const labels = texts.buttonLabels;
    if (cameraOn && microphoneOn) return labels.CAMERA_AND_MIC;
    if (cameraOn) return labels.CAMERA_ONLY;
    if (microphoneOn) return labels.MIC_ONLY;
    return labels.NEITHER;
}

function render() {
    const cameraOn = state.videoEnabled;
    const microphoneOn = state.audioEnabled;
    renderDeviceToggle(microphoneButton, microphoneOn);
    renderDeviceToggle(cameraButton, cameraOn);
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

function showTexts() {
    titleElement.textContent = texts.title;
    headingElement.textContent = texts.heading;
    subLineElement.textContent = texts.subLine;
    subLineElement.classList.toggle("hidden", texts.subLine === "");
}

export function openPrejoin(kind) {
    openKind = kind;
    texts = KIND_TEXTS[kind];
    showTexts();
    showScreen(SCREEN.PREJOIN);
    whileMediaIsRequested(openMedia).catch(showMediaFailure);
}

export function initPrejoin({ onStart }) {
    microphoneButton.addEventListener("click", toggleMicrophone);
    cameraButton.addEventListener("click", () => {
        void toggleCamera();
    });
    startButton.addEventListener("click", () => onStart(openKind));
    for (const button of wayHomeButtons) {
        button.addEventListener("click", goHome);
    }
}
