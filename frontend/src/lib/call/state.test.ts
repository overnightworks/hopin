import { describe, expect, it } from "vitest";
import { state } from "./state.ts";

describe("call state", () => {
    it("starts a fresh call not hosting, unlocked, with audio and video on and nothing joined", () => {
        expect(state).toEqual({
            roomId: null,
            isHost: false,
            isLocked: false,
            reconnectAttempt: 0,
            intentionalLeave: false,
            localStream: null,
            screenStream: null,
            audioEnabled: true,
            videoEnabled: true,
            namesByParticipant: new Map(),
        });
    });

    it("shares one object between importers", async () => {
        const second = await import("#lib/call/state.ts");
        expect(second.state).toBe(state);
    });
});
