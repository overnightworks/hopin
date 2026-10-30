import { describe, expect, it } from "vitest";

import {
    hideParticipantCount,
    lockTag,
    participantCount,
    setStatus,
    showLockIndicator,
    showParticipantCount,
    statusLine,
    type StatusType,
} from "#lib/ui/status.svelte.ts";

describe("status line", () => {
    it("starts as not connected", () => {
        expect(statusLine).toEqual({ text: "Not connected", type: "disconnected" });
    });

    it.each<StatusType>(["connecting", "connected", "error", "disconnected"])(
        "shows the text with the %s type",
        (type) => {
            setStatus("Some text", type);

            expect(statusLine).toEqual({ text: "Some text", type });
        },
    );
});

describe("lock tag", () => {
    it("is hidden at first, shown when locked and hidden when unlocked", () => {
        expect(lockTag.isShown).toBe(false);

        showLockIndicator(true);
        expect(lockTag.isShown).toBe(true);

        showLockIndicator(false);
        expect(lockTag.isShown).toBe(false);
    });
});

describe("participant count", () => {
    it("is hidden at first, shows its number, then hides again", () => {
        expect(participantCount.isShown).toBe(false);

        showParticipantCount(3);
        expect(participantCount).toEqual({ count: 3, isShown: true });

        hideParticipantCount();
        expect(participantCount.isShown).toBe(false);
    });
});
