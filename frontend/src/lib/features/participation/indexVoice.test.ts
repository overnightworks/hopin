// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { AGENT_ACTIVITY } from "#lib/call/feature-port.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { installedParticipation, tileActivities } from "./page.fixture.ts";
import type { FakeSound } from "./player.fixture.ts";
import { FIGURE_MOOD, PARTICIPATION_SIGNAL, VOICE_PATH } from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
});

const joined = (sponsorId: string, agentId = "a1") => ({
    agent_id: agentId,
    name: "Claude",
    sponsor: "Ada",
    sponsor_id: sponsorId,
});

describe("an agent's voice", () => {
    async function pageWithAgents(...agentIds: string[]) {
        const page = await installedParticipation();
        page.seat(false);
        for (const agentId of agentIds) page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", agentId));
        return page;
    }

    const voiceOf = (agentId: string, voiceId: string) => ({ agent_id: agentId, voice_id: voiceId });

    it("CALL-60: plays each voice through the player of its agent, in the order the frames arrive", async () => {
        const page = await pageWithAgents("a1");

        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "second"));

        expect(page.players.created.map((player) => player.sounds.map(({ sound }) => sound))).toEqual([
            [`${VOICE_PATH}/first`, `${VOICE_PATH}/second`],
        ]);
    });

    it("CALL-60: shows the agent speaking from the start of its sound to the end of it, with the tile's other fields kept", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        const [sound] = page.players.created[0]?.sounds ?? [];

        const before = tileActivities(page);
        await sound?.start();
        const whilePlaying = [...page.tiles.values()].map(({ activity, name, sponsor }) => [activity, name, sponsor]);
        await sound?.end({ kind: "played", playedSeconds: 1 });

        expect([before, whilePlaying, tileActivities(page)]).toEqual([
            [AGENT_ACTIVITY.PRESENT],
            [[AGENT_ACTIVITY.SPEAKING, "Claude", "Ada"]],
            [AGENT_ACTIVITY.PRESENT],
        ]);
    });

    it("CALL-60: shows the agent present again when the server stops its voice, and plays the voice that follows", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();

        page.emit(PARTICIPATION_SIGNAL.AGENT_STOP, { agent_id: "a1" });
        await Promise.resolve();
        await Promise.resolve();
        const afterStop = tileActivities(page);
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "later"));
        await page.players.created[0]?.sounds[1]?.start();

        expect([afterStop, tileActivities(page)]).toEqual([[AGENT_ACTIVITY.PRESENT], [AGENT_ACTIVITY.SPEAKING]]);
    });

    it.each([
        { end: "plays out", ends: (sound?: FakeSound) => sound?.end({ kind: "played", playedSeconds: 1 }) },
        { end: "breaks off", ends: (sound?: FakeSound) => sound?.end({ kind: "failed" }) },
    ])(
        "CALL-67: hands the agent's figure its player's node and the sentence's mood while its sound plays, and no sentence once it $end",
        async ({ ends }) => {
            const page = await pageWithAgents("a1");
            page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, { ...voiceOf("a1", "first"), mood: FIGURE_MOOD.HAPPY });
            const [player] = page.players.created;

            await player?.sounds[0]?.start();
            await ends(player?.sounds[0]);

            expect(page.figureSentences).toEqual([
                ["agent-a1", { voice: player?.tap, mood: FIGURE_MOOD.HAPPY }],
                ["agent-a1", null],
            ]);
        },
    );

    it("CALL-67: hands the figure no sentence once the server cuts the agent's sound", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();

        page.emit(PARTICIPATION_SIGNAL.AGENT_STOP, { agent_id: "a1" });
        await Promise.resolve();
        await Promise.resolve();

        expect(page.figureSentences.at(-1)).toEqual(["agent-a1", null]);
    });

    it("CALL-67: hands the figure each next sentence as it starts while the agent keeps speaking, one without a mood with the voice alone", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, { ...voiceOf("a1", "first"), mood: FIGURE_MOOD.SAD });
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "second"));
        const [player] = page.players.created;

        await player?.sounds[0]?.start();
        await player?.sounds[1]?.start();
        await player?.sounds[0]?.end({ kind: "played", playedSeconds: 1 });

        expect(page.figureSentences).toEqual([
            ["agent-a1", { voice: player?.tap, mood: FIGURE_MOOD.SAD }],
            ["agent-a1", { voice: player?.tap }],
        ]);
    });

    it("CALL-67: hands the figure nothing for a sound the browser refuses to play", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, { ...voiceOf("a1", "first"), mood: FIGURE_MOOD.HAPPY });

        await page.players.created[0]?.sounds[0]?.end({ kind: "refused" });

        expect(page.figureSentences).toEqual([]);
    });

    it("CALL-64: hands a gesture the server tells to the figure of the agent it names", async () => {
        const page = await pageWithAgents("a1", "a2");

        page.emit(PARTICIPATION_SIGNAL.AGENT_GESTURE, { agent_id: "a2", gesture: "wave" });

        expect(page.gestures).toEqual([["agent-a2", "wave"]]);
    });

    it("CALL-64: drops a gesture of an agent the stage does not show", async () => {
        const page = await pageWithAgents("a1");

        page.emit(PARTICIPATION_SIGNAL.AGENT_GESTURE, { agent_id: "unknown", gesture: "wave" });

        expect(page.gestures).toEqual([]);
    });

    it("CALL-60: shows only the agent whose sound plays as speaking while two agents' sounds overlap", async () => {
        const page = await pageWithAgents("a1", "a2");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a2", "second"));

        await page.players.created[0]?.sounds[0]?.start();
        await page.players.created[1]?.sounds[0]?.start();
        await page.players.created[0]?.sounds[0]?.end({ kind: "played", playedSeconds: 1 });

        expect([...page.tiles.entries()].map(([key, { activity }]) => [key, activity])).toEqual([
            ["agent-a1", AGENT_ACTIVITY.PRESENT],
            ["agent-a2", AGENT_ACTIVITY.SPEAKING],
        ]);
    });

    it("CALL-60: shows no error when the browser refuses to play a sound", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));

        await page.players.created[0]?.sounds[0]?.end({ kind: "refused" });

        expect([page.toasts, tileActivities(page)]).toEqual([[], [AGENT_ACTIVITY.PRESENT]]);
    });

    it("CALL-60: keeps the speaking tile when its agent is announced again to the same seat", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a1"));

        expect(tileActivities(page)).toEqual([AGENT_ACTIVITY.SPEAKING]);
    });

    it("CALL-60: closes the player of an agent that leaves and plays no later voice of it", async () => {
        const page = await pageWithAgents("a1", "a2");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a2", "second"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "late"));

        expect(page.players.created.map((player) => [player.closed, player.sounds.length])).toEqual([
            [true, 1],
            [false, 1],
        ]);
    });

    it("CALL-60: plays no voice of an agent that was never announced", async () => {
        const page = await pageWithAgents();

        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));

        expect(page.players.created).toEqual([]);
    });

    it("CALL-60: closes every player when the call ends", async () => {
        const page = await pageWithAgents("a1", "a2");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a2", "second"));

        page.endCall();

        expect(page.players.created.map((player) => player.closed)).toEqual([true, true]);
    });

    it("CALL-60: closes the players of the agents a new seat is not told of", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));

        page.seat(false, SIGNAL_TYPE.EXISTING_PARTICIPANTS);

        expect(page.players.created.map((player) => player.closed)).toEqual([true]);
    });
});
