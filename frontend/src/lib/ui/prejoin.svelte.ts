import { flushSync } from "svelte";

import { acquireMedia, addCamera, releaseCamera, toggleAudio } from "#lib/call/media.ts";
import { showLocalStream, updateLocalPreview } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";
import { SCREEN, showScreen } from "#lib/ui/screens.svelte.ts";

export const PREJOIN_KIND = {
    HOST: "host",
    GUEST: "guest",
} as const;

export type PrejoinKind = (typeof PREJOIN_KIND)[keyof typeof PREJOIN_KIND];

interface KindTexts {
    title: string;
    heading: string;
    subLine: string;
    buttonLabels: { cameraAndMic: string; cameraOnly: string; micOnly: string; neither: string };
}

const KIND_TEXTS: Record<PrejoinKind, KindTexts> = {
    [PREJOIN_KIND.HOST]: {
        title: "New call",
        heading: "Ready to start?",
        subLine: "You'll get a link to invite people.",
        buttonLabels: {
            cameraAndMic: "Start with camera and mic",
            cameraOnly: "Start with camera only",
            micOnly: "Start with mic only",
            neither: "Start with camera and mic off",
        },
    },
    [PREJOIN_KIND.GUEST]: {
        title: "Join call",
        heading: "Ready to join?",
        subLine: "",
        buttonLabels: {
            cameraAndMic: "Join with camera and mic",
            cameraOnly: "Join with camera only",
            micOnly: "Join with mic only",
            neither: "Join with camera and mic off",
        },
    },
};

const UNOPENED_HEADING = "HopIn";

export const prejoin: {
    kind: PrejoinKind | null;
    isMediaPending: boolean;
    failureMessage: string;
    isMicrophoneOn: boolean;
    isCameraOn: boolean;
    hasStream: boolean;
} = $state({
    kind: null,
    isMediaPending: false,
    failureMessage: "",
    isMicrophoneOn: true,
    isCameraOn: true,
    hasStream: false,
});

let onStartRequested: ((kind: PrejoinKind) => void) | null = null;

export function installPrejoinStart(callback: (kind: PrejoinKind) => void) {
    if (onStartRequested) throw new Error("The pre-join start is already installed");
    onStartRequested = callback;
}

export function prejoinTexts() {
    if (!prejoin.kind) return { title: "", heading: UNOPENED_HEADING, subLine: "" };
    const { title, heading, subLine } = KIND_TEXTS[prejoin.kind];
    return { title, heading, subLine };
}

export function startLabel() {
    if (!prejoin.kind) return "";
    const labels = KIND_TEXTS[prejoin.kind].buttonLabels;
    if (prejoin.isCameraOn && prejoin.isMicrophoneOn) return labels.cameraAndMic;
    if (prejoin.isCameraOn) return labels.cameraOnly;
    if (prejoin.isMicrophoneOn) return labels.micOnly;
    return labels.neither;
}

export function isPrejoinInert() {
    return prejoin.isMediaPending || !prejoin.hasStream;
}

// The e2e suite reads the DOM right after a click.
function render() {
    prejoin.isMicrophoneOn = state.audioEnabled;
    prejoin.isCameraOn = state.videoEnabled;
    prejoin.hasStream = Boolean(state.localStream);
    flushSync();
}

// A press while a request is pending would act on media that is not there yet,
// so every control waits for the answer.
async function whileMediaIsRequested(request: () => Promise<unknown>) {
    prejoin.isMediaPending = true;
    render();
    try {
        await request();
    } finally {
        prejoin.isMediaPending = false;
        render();
    }
}

async function openMedia() {
    await acquireMedia();
    showLocalStream(state.localStream);
    updateLocalPreview();
}

function showMediaFailure(failure: Error) {
    prejoin.failureMessage = failure.message;
    flushSync();
}

export function microphoneClicked() {
    toggleAudio();
    render();
}

export async function cameraClicked() {
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

export function startClicked() {
    if (!onStartRequested || !prejoin.kind) throw new Error("The pre-join is not open");
    onStartRequested(prejoin.kind);
}

export function openPrejoin(kind: PrejoinKind) {
    prejoin.kind = kind;
    showScreen(SCREEN.PREJOIN);
    whileMediaIsRequested(openMedia).catch(showMediaFailure);
}
