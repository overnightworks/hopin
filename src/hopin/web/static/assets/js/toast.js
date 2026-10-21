export const TOAST_KIND = Object.freeze({ DONE: "done", FAILED: "failed" });

const TOAST_MS = 2400;
const TEMPLATE_OF_KIND = {
    [TOAST_KIND.DONE]: document.getElementById("toastDoneTemplate"),
    [TOAST_KIND.FAILED]: document.getElementById("toastFailedTemplate"),
};

const layer = document.getElementById("toasts");
let leavingTimer = null;

export function showToast(text, kind) {
    const toast = TEMPLATE_OF_KIND[kind].content.firstElementChild.cloneNode(true);
    toast.querySelector("span").textContent = text;
    layer.replaceChildren(toast);
    clearTimeout(leavingTimer);
    leavingTimer = setTimeout(() => {
        toast.classList.add("leaving");
        void Promise.allSettled(toast.getAnimations().map((animation) => animation.finished)).then(() => toast.remove());
    }, TOAST_MS);
}

export function clearToast() {
    clearTimeout(leavingTimer);
    layer.replaceChildren();
}
