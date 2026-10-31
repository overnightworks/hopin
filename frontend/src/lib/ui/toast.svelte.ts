import { tick } from "svelte";

export const TOAST_KIND = Object.freeze({
    DONE: "done",
    FAILED: "failed",
    LOCKED: "locked",
    WARNING: "warning",
    WAIT: "wait",
});

export type ToastKind = (typeof TOAST_KIND)[keyof typeof TOAST_KIND];
export type ToastAction = { label: string; onAct: () => void };
export type Toast = {
    id: number;
    text: string;
    kind: ToastKind;
    action: ToastAction | null;
    leaving: boolean;
};

const TOAST_MS = 2400;
// A toast with an action stays long enough to be reached and tapped.
const TOAST_WITH_ACTION_MS = 8000;

export const toastState: { current: Toast | null } = $state({ current: null });

// Toasts.svelte binds its rendered toast element here; the element is plain DOM, so it stays out of $state.
export const toastElement: { current: HTMLElement | null } = { current: null };

let nextId = 0;
let leavingTimer: ReturnType<typeof setTimeout> | undefined;

async function removeAfterLeaving(toast: Toast) {
    toast.leaving = true;
    await tick();
    const animations = toastElement.current?.getAnimations() ?? [];
    await Promise.allSettled(animations.map((animation) => animation.finished));
    if (toastState.current?.id === toast.id) toastState.current = null;
}

export function showToast(text: string, kind: ToastKind, action: ToastAction | null = null) {
    const toast: Toast = $state({ id: nextId++, text, kind, action, leaving: false });
    toastState.current = toast;
    clearTimeout(leavingTimer);
    leavingTimer = setTimeout(() => void removeAfterLeaving(toast), action === null ? TOAST_MS : TOAST_WITH_ACTION_MS);
}

export function clearToast() {
    clearTimeout(leavingTimer);
    toastState.current = null;
}
