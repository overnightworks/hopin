// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

// The module holds the installed callback, so each case loads a fresh copy.
async function freshLeaveDialog() {
    vi.resetModules();
    return import("#lib/ui/leave-dialog.svelte.ts");
}

describe("leave dialog", () => {
    it("starts closed and opens when shown", async () => {
        const dialog = await freshLeaveDialog();
        expect(dialog.leaveDialog.isOpen).toBe(false);

        dialog.showLeaveDialog();

        expect(dialog.leaveDialog.isOpen).toBe(true);
    });

    it("closes on cancel without calling the callback", async () => {
        const dialog = await freshLeaveDialog();
        const onLeaveConfirmed = vi.fn();
        dialog.installLeaveConfirmed(onLeaveConfirmed);
        dialog.showLeaveDialog();

        dialog.cancelLeave();

        expect(dialog.leaveDialog.isOpen).toBe(false);
        expect(onLeaveConfirmed).not.toHaveBeenCalled();
    });

    it("closes on confirm and calls the callback once", async () => {
        const dialog = await freshLeaveDialog();
        let openWhenCalled: boolean | undefined;
        const onLeaveConfirmed = vi.fn(() => {
            openWhenCalled = dialog.leaveDialog.isOpen;
        });
        dialog.installLeaveConfirmed(onLeaveConfirmed);
        dialog.showLeaveDialog();

        dialog.confirmLeave();

        expect(dialog.leaveDialog.isOpen).toBe(false);
        expect(openWhenCalled).toBe(false);
        expect(onLeaveConfirmed).toHaveBeenCalledOnce();
    });

    it("throws on confirm before a callback is installed", async () => {
        const dialog = await freshLeaveDialog();

        expect(() => {
            dialog.confirmLeave();
        }).toThrow(/not installed/);
    });

    it("throws on a second install and keeps the first callback", async () => {
        const dialog = await freshLeaveDialog();
        const first = vi.fn();
        const second = vi.fn();
        dialog.installLeaveConfirmed(first);

        expect(() => {
            dialog.installLeaveConfirmed(second);
        }).toThrow(/already installed/);
        dialog.confirmLeave();

        expect(first).toHaveBeenCalledOnce();
        expect(second).not.toHaveBeenCalled();
    });
});

describe("leaving on pagehide", () => {
    async function pagehide(persisted: boolean, inCall: boolean) {
        const dialog = await freshLeaveDialog();
        const { state } = await import("#lib/call/state.ts");
        state.inCall = inCall;
        const leave = vi.fn();
        dialog.leaveOnPagehide({ persisted }, leave);
        return leave;
    }

    it("CALL-10: a pagehide that enters the back-forward cache keeps the call", async () => {
        expect(await pagehide(true, true)).not.toHaveBeenCalled();
    });

    it("CALL-10: a pagehide that unloads a connected page leaves once", async () => {
        expect(await pagehide(false, true)).toHaveBeenCalledOnce();
    });

    it("CALL-10: a pagehide of a page that is not in a call leaves nothing", async () => {
        expect(await pagehide(false, false)).not.toHaveBeenCalled();
    });
});
