// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

// The module reads the navigator once and holds its state, so each case loads a fresh copy.
async function freshInvite() {
    vi.resetModules();
    const invite = await import("#lib/ui/invite.svelte.ts");
    const toast = await import("#lib/ui/toast.svelte.ts");
    return { ...invite, toastState: toast.toastState, TOAST_KIND: toast.TOAST_KIND };
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe("invite link and waiting card", () => {
    it("starts without a link and with the waiting card hidden", async () => {
        const { invite } = await freshInvite();

        expect(invite.link).toBe("");
        expect(invite.isWaitingCardShown).toBe(false);
    });

    it("stores the call link", async () => {
        const { invite, setCallLink } = await freshInvite();

        setCallLink("https://example.test/c/abc");

        expect(invite.link).toBe("https://example.test/c/abc");
    });

    it("shows and hides the waiting card", async () => {
        const { invite, showWaitingCard, hideWaitingCard } = await freshInvite();

        showWaitingCard();
        expect(invite.isWaitingCardShown).toBe(true);

        hideWaitingCard();
        expect(invite.isWaitingCardShown).toBe(false);
    });

    it.each([
        ["available", { share: vi.fn() }],
        ["unavailable", {}],
    ])("reports share as %s from the navigator", async (expected, navigatorStub) => {
        vi.stubGlobal("navigator", navigatorStub);

        const { invite } = await freshInvite();

        expect(invite.share).toBe(expected);
    });
});

describe("copying the link", () => {
    it("writes the link to the clipboard and toasts done", async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        vi.stubGlobal("navigator", { clipboard: { writeText } });
        const { copyCallLink, setCallLink, toastState, TOAST_KIND } = await freshInvite();
        setCallLink("https://example.test/c/abc");

        await copyCallLink();

        expect(writeText).toHaveBeenCalledWith("https://example.test/c/abc");
        expect(toastState.current).toMatchObject({ text: "Link copied", kind: TOAST_KIND.DONE });
    });

    it("toasts failed when the clipboard rejects", async () => {
        vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
        const { copyCallLink, toastState, TOAST_KIND } = await freshInvite();

        await copyCallLink();

        expect(toastState.current).toMatchObject({ text: "Couldn't copy the link", kind: TOAST_KIND.FAILED });
    });
});

describe("sharing the link", () => {
    it("shares the link silently on success", async () => {
        const share = vi.fn().mockResolvedValue(undefined);
        vi.stubGlobal("navigator", { share });
        const { setCallLink, shareCallLink, toastState } = await freshInvite();
        setCallLink("https://example.test/c/abc");

        await shareCallLink();

        expect(share).toHaveBeenCalledWith({ url: "https://example.test/c/abc" });
        expect(toastState.current).toBeNull();
    });

    it("stays silent when the person cancels", async () => {
        const cancelled = Object.assign(new Error("cancelled"), { name: "AbortError" });
        vi.stubGlobal("navigator", { share: vi.fn().mockRejectedValue(cancelled) });
        const { shareCallLink, toastState } = await freshInvite();

        await shareCallLink();

        expect(toastState.current).toBeNull();
    });

    it("toasts failed on any other error", async () => {
        vi.stubGlobal("navigator", { share: vi.fn().mockRejectedValue(new Error("boom")) });
        const { shareCallLink, toastState, TOAST_KIND } = await freshInvite();

        await shareCallLink();

        expect(toastState.current).toMatchObject({ text: "Couldn't share the link", kind: TOAST_KIND.FAILED });
    });
});
