// The leave confirmation's open state; the start-up module installs its confirm action, because a `lib/ui` state
// module never imports the module that imports it.

import { state } from "#lib/call/state.ts";

export const leaveDialog = $state({ isOpen: false });

let onLeaveConfirmed: (() => void) | null = null;

export function installLeaveConfirmed(callback: () => void) {
    if (onLeaveConfirmed) throw new Error("The leave confirmation is already installed");
    onLeaveConfirmed = callback;
}

export function showLeaveDialog() {
    leaveDialog.isOpen = true;
}

export function cancelLeave() {
    leaveDialog.isOpen = false;
}

export function confirmLeave() {
    if (!onLeaveConfirmed) throw new Error("The leave confirmation is not installed");
    leaveDialog.isOpen = false;
    onLeaveConfirmed();
}

// A page entering the back-forward cache stays in the call; one that unloads ends it with the close code 1000.
export function leaveOnPagehide(event: Pick<PageTransitionEvent, "persisted">, leave: () => void) {
    if (!event.persisted && state.inCall) leave();
}
