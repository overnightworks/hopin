export type StatusType = "connecting" | "connected" | "error" | "disconnected";

export const statusLine: { text: string; type: StatusType } = $state({
    text: "Not connected",
    type: "disconnected",
});
export const lockTag = $state({ isShown: false });
export const participantCount = $state({ count: 1, isShown: false });

export function setStatus(text: string, type: StatusType) {
    statusLine.text = text;
    statusLine.type = type;
}

export function showLockIndicator(isLocked: boolean) {
    lockTag.isShown = isLocked;
}

export function showParticipantCount(count: number) {
    participantCount.count = count;
    participantCount.isShown = true;
}

export function hideParticipantCount() {
    participantCount.isShown = false;
}
