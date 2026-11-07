// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { AGENT_ACTIVITY, type AgentTile, type FeatureCore } from "#lib/call/feature-port.ts";
import { TOAST_KIND } from "#lib/ui/toast.svelte.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { install } from "./index.ts";
import { PARTICIPATION_SIGNAL } from "./wire.ts";

const ME = "me";

async function installedParticipation() {
    const handlers = new Map<string, (frame: never) => void>();
    const sent: unknown[] = [];
    const tiles = new Map<string, AgentTile>();
    const toasts: string[] = [];
    const callEndedHooks: (() => void)[] = [];
    const core: FeatureCore = {
        on: (type, handler) => void handlers.set(type, handler),
        send: (frame) => void sent.push(frame),
        isConnected: () => true,
        showToast: (text) => void toasts.push(text),
        toastKind: TOAST_KIND,
        showAgentTile: (tile, key = "agent") => {
            if (tile === null) tiles.delete(key);
            else tiles.set(key, tile);
        },
        addMoreItems: () => undefined,
        receiveChatLine: () => undefined,
        pinLastChatLine: () => undefined,
        showChatCard: () => undefined,
        onCallEnded: (hook) => void callEndedHooks.push(hook),
    };
    await install(core);
    const emit = (type: string, frame: object) => {
        const handler = handlers.get(type);
        if (handler === undefined) throw new Error(`no handler for ${type}`);
        (handler as (frame: object) => void)(frame);
    };
    return {
        emit,
        sent,
        tiles,
        toasts,
        endCall: () => {
            callEndedHooks.forEach((hook) => {
                hook();
            });
        },
        seat: (isHost: boolean, signal: string = SIGNAL_TYPE.ROOM_CREATED) => {
            emit(signal, { participant_id: ME, is_host: isHost });
        },
    };
}

const joined = (sponsorId: string, agentId = "a1") => ({
    agent_id: agentId,
    name: "Claude",
    sponsor: "Ada",
    sponsor_id: sponsorId,
});

describe("dialed-in agents", () => {
    it("CALL-50: shows the tile under the name its sponsor gave, with the sponsor's name", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        expect([...page.tiles.values()].map(({ name, sponsor, activity }) => [name, sponsor, activity])).toEqual([
            ["Claude", "Ada", AGENT_ACTIVITY.PRESENT],
        ]);
    });

    it.each([
        { who: "the sponsor", host: false, sponsorId: ME },
        { who: "the host", host: true, sponsorId: "someone" },
    ])("CALL-49: offers the remove action to $who, and it asks the server to remove the agent", async (arrival) => {
        const page = await installedParticipation();
        page.seat(arrival.host, SIGNAL_TYPE.EXISTING_PARTICIPANTS);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(arrival.sponsorId));

        page.tiles.get("agent-a1")?.onRemove?.();

        expect(page.sent).toEqual([{ type: PARTICIPATION_SIGNAL.AGENT_REMOVE, agent_id: "a1" }]);
    });

    it("CALL-49: offers no remove action to anyone else", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        expect(page.tiles.get("agent-a1")?.onRemove).toBeUndefined();
    });

    it("CALL-49: tells a person whose removal was refused who may remove", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: "not_sponsor_or_host" });

        expect(page.toasts).toEqual(["Only the person who brought the agent or the host can remove it."]);
    });

    it("takes the tile away when its agent leaves, and shows a repeated join once", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));
        const whileIn = page.tiles.size;

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });

        expect([whileIn, page.tiles.size]).toEqual([1, 0]);
    });

    it("takes every tile away when the call ends", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a1"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a2"));

        page.endCall();

        expect(page.tiles.size).toBe(0);
    });
});
