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
