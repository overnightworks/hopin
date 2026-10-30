// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The module holds the one current toast and its timer, so each case loads a fresh copy.
async function freshToast() {
    vi.resetModules();
    return import("#lib/ui/toast.svelte.ts");
}

function renderToastLayer(animations: { finished: Promise<unknown> }[]) {
    document.body.innerHTML = '<output id="toasts"><div class="toast"></div></output>';
    const element = document.querySelector(".toast") as HTMLElement;
    element.getAnimations = (() => animations) as unknown as HTMLElement["getAnimations"];
}

beforeEach(() => {
    vi.useFakeTimers();
});

afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
});

describe("toast", () => {
    it("has no toast before one is shown", async () => {
        const toast = await freshToast();
        expect(toast.toastState.current).toBeNull();
    });

    it("keeps the five kind values the game reads", async () => {
        const toast = await freshToast();
        expect(Object.values(toast.TOAST_KIND)).toEqual(["done", "failed", "locked", "warning", "wait"]);
    });

    it.each(["done", "failed", "locked", "warning", "wait"] as const)(
        "shows its text for the %s kind",
        async (kind) => {
            const toast = await freshToast();

            toast.showToast("Hello", kind);

            expect(toast.toastState.current).toMatchObject({ text: "Hello", kind, action: null, leaving: false });
        },
    );

    it("replaces the first toast with a new one", async () => {
        const toast = await freshToast();
        toast.showToast("First", toast.TOAST_KIND.DONE);
        const first = toast.toastState.current;

        toast.showToast("Second", toast.TOAST_KIND.FAILED);

        expect(toast.toastState.current?.text).toBe("Second");
        expect(toast.toastState.current?.id).not.toBe(first?.id);
    });

    it("starts leaving after 2400 ms", async () => {
        const toast = await freshToast();
        toast.showToast("Hi", toast.TOAST_KIND.DONE);

        await vi.advanceTimersByTimeAsync(2399);
        expect(toast.toastState.current?.leaving).toBe(false);
        renderToastLayer([{ finished: new Promise(() => {}) }]);
        await vi.advanceTimersByTimeAsync(1);

        expect(toast.toastState.current?.leaving).toBe(true);
    });

    it("starts leaving after 8000 ms when it carries an action", async () => {
        const toast = await freshToast();
        toast.showToast("Hi", toast.TOAST_KIND.WAIT, { label: "Undo", onAct: vi.fn() });
        renderToastLayer([{ finished: new Promise(() => {}) }]);

        await vi.advanceTimersByTimeAsync(7999);
        expect(toast.toastState.current?.leaving).toBe(false);
        await vi.advanceTimersByTimeAsync(1);

        expect(toast.toastState.current?.leaving).toBe(true);
    });

    it("hands the action back so the button can call it", async () => {
        const toast = await freshToast();
        const onAct = vi.fn();
        toast.showToast("Hi", toast.TOAST_KIND.WAIT, { label: "Undo", onAct });

        toast.toastState.current?.action?.onAct();

        expect(toast.toastState.current?.action?.label).toBe("Undo");
        expect(onAct).toHaveBeenCalledOnce();
    });

    it("cancels the first toast's timer when a second one comes", async () => {
        const toast = await freshToast();
        toast.showToast("First", toast.TOAST_KIND.DONE);
        await vi.advanceTimersByTimeAsync(2000);

        toast.showToast("Second", toast.TOAST_KIND.DONE);
        await vi.advanceTimersByTimeAsync(1000);

        expect(toast.toastState.current?.leaving).toBe(false);
    });

    it("removes the toast only once its animations finish", async () => {
        const toast = await freshToast();
        let finish: () => void = () => {};
        renderToastLayer([{ finished: new Promise<void>((resolve) => (finish = resolve)) }]);
        toast.showToast("Hi", toast.TOAST_KIND.DONE);

        await vi.advanceTimersByTimeAsync(2400);
        expect(toast.toastState.current?.leaving).toBe(true);
        finish();
        await vi.advanceTimersByTimeAsync(0);

        expect(toast.toastState.current).toBeNull();
    });

    it("removes the toast even when an animation was cancelled", async () => {
        const toast = await freshToast();
        renderToastLayer([
            {
                get finished() {
                    return Promise.reject(new Error("cancelled"));
                },
            },
        ]);
        toast.showToast("Hi", toast.TOAST_KIND.DONE);

        await vi.advanceTimersByTimeAsync(2400);

        expect(toast.toastState.current).toBeNull();
    });

    it("removes a leaving toast when no element is rendered", async () => {
        const toast = await freshToast();
        toast.showToast("Hi", toast.TOAST_KIND.DONE);

        await vi.advanceTimersByTimeAsync(2400);

        expect(toast.toastState.current).toBeNull();
    });

    it("keeps a newer toast when the older one's animations finish late", async () => {
        const toast = await freshToast();
        let finish: () => void = () => {};
        renderToastLayer([{ finished: new Promise<void>((resolve) => (finish = resolve)) }]);
        toast.showToast("First", toast.TOAST_KIND.DONE);
        await vi.advanceTimersByTimeAsync(2400);

        toast.showToast("Second", toast.TOAST_KIND.DONE);
        finish();
        await vi.advanceTimersByTimeAsync(0);

        expect(toast.toastState.current?.text).toBe("Second");
    });

    it("clears the toast at once and cancels its timer", async () => {
        const toast = await freshToast();
        toast.showToast("Hi", toast.TOAST_KIND.DONE);

        toast.clearToast();
        await vi.advanceTimersByTimeAsync(10000);

        expect(toast.toastState.current).toBeNull();
    });
});
