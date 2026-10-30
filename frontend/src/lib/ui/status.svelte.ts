import { flushSync } from "svelte";

export type StatusType = "connecting" | "connected" | "error" | "disconnected";

export const statusLine: { text: string; type: StatusType } = $state({
    text: "Not connected",
    type: "disconnected",
});
export const lockTag = $state({ isShown: false });
export const participantCount = $state({ count: 1, isShown: false });

// Legacy code and the end-to-end suite read the DOM right after a write.
export function setStatus(text: string, type: StatusType) {
    statusLine.text = text;
    statusLine.type = type;
    flushSync();
}

export function showLockIndicator(isLocked: boolean) {
    lockTag.isShown = isLocked;
    flushSync();
}

export function showParticipantCount(count: number) {
    participantCount.count = count;
    participantCount.isShown = true;
    flushSync();
}

export function hideParticipantCount() {
    participantCount.isShown = false;
    flushSync();
}
