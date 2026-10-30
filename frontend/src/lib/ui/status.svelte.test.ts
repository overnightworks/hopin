// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

import type { StatusType } from "#lib/ui/status.svelte.ts";

// The module holds its state, so each case loads a fresh copy.
async function freshStatus() {
    vi.resetModules();
    return import("#lib/ui/status.svelte.ts");
}

describe("status line", () => {
    it("starts as not connected", async () => {
        const { statusLine } = await freshStatus();

        expect(statusLine).toEqual({ text: "Not connected", type: "disconnected" });
    });

    it.each<StatusType>(["connecting", "connected", "error", "disconnected"])(
        "shows the text with the %s type",
        async (type) => {
            const { setStatus, statusLine } = await freshStatus();

            setStatus("Some text", type);

            expect(statusLine).toEqual({ text: "Some text", type });
        },
    );
});

describe("lock tag", () => {
    it("is hidden at first, shown when locked and hidden when unlocked", async () => {
        const { lockTag, showLockIndicator } = await freshStatus();
        expect(lockTag.isShown).toBe(false);

        showLockIndicator(true);
        expect(lockTag.isShown).toBe(true);

        showLockIndicator(false);
        expect(lockTag.isShown).toBe(false);
    });
});

describe("participant count", () => {
    it("is hidden at first, shows its number, then hides again", async () => {
        const { hideParticipantCount, participantCount, showParticipantCount } = await freshStatus();
        expect(participantCount.isShown).toBe(false);

        showParticipantCount(3);
        expect(participantCount).toEqual({ count: 3, isShown: true });

        hideParticipantCount();
        expect(participantCount.isShown).toBe(false);
    });
});
