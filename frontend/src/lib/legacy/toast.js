export const TOAST_KIND = Object.freeze({
    DONE: "done",
    FAILED: "failed",
    LOCKED: "locked",
    WARNING: "warning",
    WAIT: "wait",
});

const TOAST_MS = 2400;
// A toast with an action stays long enough to be reached and tapped.
const TOAST_WITH_ACTION_MS = 8000;
const TEMPLATE_OF_KIND = {
    [TOAST_KIND.DONE]: document.getElementById("toastDoneTemplate"),
    [TOAST_KIND.FAILED]: document.getElementById("toastFailedTemplate"),
    [TOAST_KIND.LOCKED]: document.getElementById("toastLockedTemplate"),
    [TOAST_KIND.WARNING]: document.getElementById("toastWarningTemplate"),
    [TOAST_KIND.WAIT]: document.getElementById("toastWaitTemplate"),
};

const layer = document.getElementById("toasts");
let leavingTimer = null;

function actionButton({ label, onAct }) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-act";
    button.textContent = label;
    button.addEventListener("click", onAct);
    return button;
}

export function showToast(text, kind, action = null) {
    const toast = TEMPLATE_OF_KIND[kind].content.firstElementChild.cloneNode(true);
    toast.querySelector("span").textContent = text;
    if (action !== null) toast.append(actionButton(action));
    layer.replaceChildren(toast);
    clearTimeout(leavingTimer);
    leavingTimer = setTimeout(
        () => {
            toast.classList.add("leaving");
            void Promise.allSettled(toast.getAnimations().map((animation) => animation.finished)).then(() =>
                toast.remove(),
            );
        },
        action === null ? TOAST_MS : TOAST_WITH_ACTION_MS,
    );
}

export function clearToast() {
    clearTimeout(leavingTimer);
    layer.replaceChildren();
}
