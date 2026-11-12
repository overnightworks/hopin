import { TOAST_KIND, showToast } from "#lib/ui/toast.svelte.ts";
import { words } from "#lib/words/de.ts";

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
        showToast(words["invite.copyFailedToast"], TOAST_KIND.FAILED);
        return;
    }
    showToast(words["invite.linkCopiedToast"], TOAST_KIND.DONE);
}

export async function shareCallLink() {
    try {
        await navigator.share({ url: invite.link });
    } catch (error) {
        if ((error as Error).name !== CANCELLED_SHARE_ERROR)
            showToast(words["invite.shareFailedToast"], TOAST_KIND.FAILED);
    }
}
