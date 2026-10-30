import { TOAST_KIND, showToast } from "#lib/ui/toast.svelte.ts";

const TEXTS = {
    COPIED: "Link copied",
    COPY_FAILED: "Couldn't copy the link",
    SHARE_FAILED: "Couldn't share the link",
};
const CANCELLED_SHARE_ERROR = "AbortError";

export const invite: { link: string; isWaitingCardShown: boolean; share: "available" | "unavailable" } = $state({
    link: "",
    isWaitingCardShown: false,
    share: "share" in navigator ? "available" : "unavailable",
});

export function setCallLink(url: string) {
    invite.link = url;
}

export function showWaitingCard() {
    invite.isWaitingCardShown = true;
}

export function hideWaitingCard() {
    invite.isWaitingCardShown = false;
}

export async function copyCallLink() {
    try {
        await navigator.clipboard.writeText(invite.link);
    } catch {
        showToast(TEXTS.COPY_FAILED, TOAST_KIND.FAILED);
        return;
    }
    showToast(TEXTS.COPIED, TOAST_KIND.DONE);
}

export async function shareCallLink() {
    try {
        await navigator.share({ url: invite.link });
    } catch (error) {
        if ((error as Error).name !== CANCELLED_SHARE_ERROR) showToast(TEXTS.SHARE_FAILED, TOAST_KIND.FAILED);
    }
}
