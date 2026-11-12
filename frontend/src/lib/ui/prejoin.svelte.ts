import { flushSync } from "svelte";

import { acquireMedia, addCamera, releaseCamera, toggleAudio } from "#lib/call/media.ts";
import { showLocalStream, updateLocalPreview } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";
import { SCREEN, showScreen } from "#lib/ui/screens.svelte.ts";
import { words } from "#lib/words/de.ts";

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
        title: words["prejoin.hostTitle"],
        heading: words["prejoin.hostHeading"],
        subLine: words["prejoin.hostSubLine"],
        buttonLabels: {
            cameraAndMic: words["prejoin.hostStartCameraAndMicrophone"],
            cameraOnly: words["prejoin.hostStartCameraOnly"],
            micOnly: words["prejoin.hostStartMicrophoneOnly"],
            neither: words["prejoin.hostStartWithoutDevices"],
        },
    },
    [PREJOIN_KIND.GUEST]: {
        title: words["prejoin.guestTitle"],
        heading: words["prejoin.guestHeading"],
        subLine: "",
        buttonLabels: {
            cameraAndMic: words["prejoin.guestJoinCameraAndMicrophone"],
            cameraOnly: words["prejoin.guestJoinCameraOnly"],
            micOnly: words["prejoin.guestJoinMicrophoneOnly"],
            neither: words["prejoin.guestJoinWithoutDevices"],
        },
    },
};

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
    if (!prejoin.kind) return { title: "", heading: words["prejoin.unopenedHeading"], subLine: "" };
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

// The browser's refusal (NotAllowedError, NotFoundError) travels as the failure's cause.
function showMediaFailure(failure: Error) {
    prejoin.failureMessage = words["prejoin.mediaRefused"]({ errorName: (failure.cause as Error).name });
    flushSync();
}

function showUnexpectedFailure() {
    prejoin.failureMessage = words["call.unexpectedError"];
    flushSync();
}

async function openMedia() {
    try {
        await acquireMedia();
    } catch (error_) {
        showMediaFailure(error_ as Error);
        return;
    }
    showLocalStream(state.localStream);
    updateLocalPreview();
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
    if (!onStartRequested) throw new Error("The pre-join start is not installed");
    if (!prejoin.kind) throw new Error("The pre-join is not open");
    onStartRequested(prejoin.kind);
}

export function openPrejoin(kind: PrejoinKind) {
    prejoin.kind = kind;
    showScreen(SCREEN.PREJOIN);
    whileMediaIsRequested(openMedia).catch(showUnexpectedFailure);
}
