import { TOAST_KIND, showToast } from "./toast.js";

const TEXTS = {
    COPIED: "Link copied",
    COPY_FAILED: "Couldn't copy the link",
    SHARE_FAILED: "Couldn't share the link",
};
const CANCELLED_SHARE_ERROR = "AbortError";

const card = document.getElementById("waitingCard");
const linkElement = document.getElementById("waitingLink");
const copyButton = document.getElementById("waitingCopyBtn");
const shareButton = document.getElementById("waitingShareBtn");
const inviteButton = document.getElementById("inviteBtn");
const inviteMenu = document.getElementById("inviteMenu");
const inviteLinkElement = document.getElementById("inviteLink");

let callLink = "";

export function setCallLink(url) {
    callLink = url;
    linkElement.textContent = url;
    inviteLinkElement.textContent = url;
    inviteButton.disabled = false;
}

export function showWaitingCard() {
    card.classList.remove("hidden");
}

export function hideWaitingCard() {
    card.classList.add("hidden");
}

export async function copyCallLink() {
    try {
        await navigator.clipboard.writeText(callLink);
    } catch {
        showToast(TEXTS.COPY_FAILED, TOAST_KIND.FAILED);
        return;
    }
    showToast(TEXTS.COPIED, TOAST_KIND.DONE);
}

export async function shareCallLink() {
    try {
        await navigator.share({ url: callLink });
    } catch (error) {
        if (error.name !== CANCELLED_SHARE_ERROR) showToast(TEXTS.SHARE_FAILED, TOAST_KIND.FAILED);
    }
}

export function initInvite() {
    const shareState = "share" in navigator ? "available" : "unavailable";
    card.dataset.share = shareState;
    inviteMenu.dataset.share = shareState;
    const copyButtons = [copyButton, ...inviteMenu.querySelectorAll("#inviteCopyBtn, #inviteCopyLinkBtn")];
    for (const button of copyButtons) {
        button.addEventListener("click", () => {
            void copyCallLink();
        });
    }
    for (const button of [shareButton, inviteMenu.querySelector("#inviteShareBtn")]) {
        button.addEventListener("click", () => {
            void shareCallLink();
        });
    }
}
