// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { AGENT_ACTIVITY, type AgentTile } from "#lib/call/feature-port.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { installedParticipation, joined, tileActivities } from "./page.fixture.ts";
import type { FakeSound } from "./player.fixture.ts";
import { FIGURE_FRAMING, FIGURE_MOOD, PARTICIPATION_SIGNAL, VOICE_PATH } from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
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

    it("CALL-70: pauses the agent's player when the server holds it, so its tile and figure leave speaking, and goes on where it paused at the continue", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        const [player] = page.players.created;
        await player?.sounds[0]?.start();

        page.emit(PARTICIPATION_SIGNAL.AGENT_HELD, { agent_id: "a1" });
        const whileHeld = [player?.paused, tileActivities(page), page.figureSentences.at(-1)?.[1]];
        page.emit(PARTICIPATION_SIGNAL.AGENT_CONTINUED, { agent_id: "a1" });

        expect([whileHeld, player?.paused, tileActivities(page), player?.sounds.length]).toEqual([
            [true, [AGENT_ACTIVITY.PRESENT], null],
            false,
            [AGENT_ACTIVITY.SPEAKING],
            1,
        ]);
    });

    it("CALL-70: holds a voice that is announced while the agent is held until the continue, and shows its tile speaking then", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_HELD, { agent_id: "a1" });
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        const [player] = page.players.created;

        await player?.sounds[0]?.start();
        const whileHeld = tileActivities(page);
        page.emit(PARTICIPATION_SIGNAL.AGENT_CONTINUED, { agent_id: "a1" });
        await Promise.resolve();
        await Promise.resolve();

        expect([whileHeld, tileActivities(page)]).toEqual([[AGENT_ACTIVITY.PRESENT], [AGENT_ACTIVITY.SPEAKING]]);
    });

    it("CALL-70: ends the sound of a stop that follows the hold and plays the voice after it, which a stop that left the pause would keep waiting", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();
        page.emit(PARTICIPATION_SIGNAL.AGENT_HELD, { agent_id: "a1" });

        page.emit(PARTICIPATION_SIGNAL.AGENT_STOP, { agent_id: "a1" });
        await Promise.resolve();
        await Promise.resolve();
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "later"));
        await page.players.created[0]?.sounds[1]?.start();

        expect([page.players.created[0]?.paused, tileActivities(page)]).toEqual([false, [AGENT_ACTIVITY.SPEAKING]]);
    });

    it("CALL-69: sends the server one report of the sentence's voice id and the seconds its player had played at the hold", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        const [player] = page.players.created;
        await player?.sounds[0]?.start();
        if (player) player.playedSeconds = 1.25;

        page.emit(PARTICIPATION_SIGNAL.AGENT_HELD, { agent_id: "a1" });

        expect(page.sent).toEqual([
            { type: PARTICIPATION_SIGNAL.AGENT_HOLD_PLAYED, agent_id: "a1", voice_id: "first", played_seconds: 1.25 },
        ]);
    });

    it("CALL-69: sends no report for a sentence its player never started", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_HELD, { agent_id: "a1" });

        expect(page.sent).toEqual([]);
    });

    it("CALL-69: sends no report for an agent this browser muted", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();
        [...page.tiles.values()][0]?.listening?.onChange(false);
        await Promise.resolve();
        await Promise.resolve();

        page.emit(PARTICIPATION_SIGNAL.AGENT_HELD, { agent_id: "a1" });

        expect(page.sent).toEqual([]);
    });

    it("CALL-70: pauses nothing for an agent the stage does not show", async () => {
        const page = await pageWithAgents();

        page.emit(PARTICIPATION_SIGNAL.AGENT_HELD, { agent_id: "unknown" });

        expect(page.players.created).toEqual([]);
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

    it("CALL-77: hands a framing the server tells to the figure of the agent it names, and drops an unknown agent's", async () => {
        const page = await pageWithAgents("a1", "a2");

        page.emit(PARTICIPATION_SIGNAL.AGENT_FRAMING, { agent_id: "a2", framing: FIGURE_FRAMING.FULL_BODY });
        page.emit(PARTICIPATION_SIGNAL.AGENT_FRAMING, { agent_id: "unknown", framing: FIGURE_FRAMING.FACE });

        expect(page.framings).toEqual([["agent-a2", "fullBody"]]);
    });

    it("CALL-78: shows the tile of a late joiner in the framing its joined frame carries, and a later frame's over it", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, {
            ...joined("someone", "a1"),
            framing: FIGURE_FRAMING.UPPER_BODY,
        });
        const first = page.tiles.get("agent-a1")?.framing;

        page.emit(PARTICIPATION_SIGNAL.AGENT_FRAMING, { agent_id: "a1", framing: FIGURE_FRAMING.FACE });

        expect([first, page.tiles.get("agent-a1")?.framing]).toEqual(["upperBody", "face"]);
    });

    it("CALL-68: hands the thinking the server tells to the figure of the agent it names, and drops an unknown agent's", async () => {
        const page = await pageWithAgents("a1", "a2");

        page.emit(PARTICIPATION_SIGNAL.AGENT_THINKING, { agent_id: "a2", on: true });
        page.emit(PARTICIPATION_SIGNAL.AGENT_THINKING, { agent_id: "unknown", on: true });
        page.emit(PARTICIPATION_SIGNAL.AGENT_THINKING, { agent_id: "a2", on: false });

        expect(page.thoughts).toEqual([
            ["agent-a2", true],
            ["agent-a2", false],
        ]);
    });

    it("CALL-68: names the agent's tile as thinking while its thinking holds and no sound of it plays", async () => {
        const page = await pageWithAgents("a1");

        page.emit(PARTICIPATION_SIGNAL.AGENT_THINKING, { agent_id: "a1", on: true });
        const whileThinking = tileActivities(page);
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();
        const whileSpeaking = tileActivities(page);
        page.emit(PARTICIPATION_SIGNAL.AGENT_THINKING, { agent_id: "a1", on: false });
        await page.players.created[0]?.sounds[0]?.end({ kind: "played", playedSeconds: 1 });

        expect([whileThinking, whileSpeaking, tileActivities(page)]).toEqual([
            [AGENT_ACTIVITY.THINKING],
            [AGENT_ACTIVITY.SPEAKING],
            [AGENT_ACTIVITY.PRESENT],
        ]);
    });

    it("CALL-68: keeps the tile speaking and the figure on its sentence while the agent states it thinks, and names the tile as thinking at once when the sound ends", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, { ...voiceOf("a1", "first"), mood: FIGURE_MOOD.HAPPY });
        const [player] = page.players.created;
        await player?.sounds[0]?.start();

        page.emit(PARTICIPATION_SIGNAL.AGENT_THINKING, { agent_id: "a1", on: true });
        const whileSounding = [tileActivities(page), [...page.figureSentences]];
        await player?.sounds[0]?.end({ kind: "played", playedSeconds: 1 });

        expect([whileSounding, tileActivities(page), page.figureSentences.at(-1), page.thoughts]).toEqual([
            [[AGENT_ACTIVITY.SPEAKING], [["agent-a1", { voice: player?.tap, mood: FIGURE_MOOD.HAPPY }]]],
            [AGENT_ACTIVITY.THINKING],
            ["agent-a1", null],
            [["agent-a1", true]],
        ]);
    });

    it("CALL-68: shows no thinking when the agent's thinking ended before its sound did", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();

        page.emit(PARTICIPATION_SIGNAL.AGENT_THINKING, { agent_id: "a1", on: true });
        page.emit(PARTICIPATION_SIGNAL.AGENT_THINKING, { agent_id: "a1", on: false });
        await page.players.created[0]?.sounds[0]?.end({ kind: "played", playedSeconds: 1 });

        expect(tileActivities(page)).toEqual([AGENT_ACTIVITY.PRESENT]);
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

describe("an agent's listen switch", () => {
    const voiceOf = (agentId: string, voiceId: string) => ({ agent_id: agentId, voice_id: voiceId });

    async function pageWithAgents(...agentIds: string[]) {
        const page = await installedParticipation();
        page.seat(false);
        for (const agentId of agentIds) page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", agentId));
        return page;
    }

    const listeningOf = (page: { tiles: Map<string, AgentTile> }) =>
        [...page.tiles.values()].map((tile) => tile.listening?.on);

    it("CALL-79: shows the switch on for every agent's tile, whoever sponsors it, and sends nothing for a press", async () => {
        const page = await pageWithAgents("a1", "a2");
        const before = listeningOf(page);

        [...page.tiles.values()][0]?.listening?.onChange(false);

        expect([before, listeningOf(page), page.sent]).toEqual([[true, true], [false, true], []]);
    });

    it("CALL-79: ends the agent's playing sound and plays none of its later ones while the switch is off, and plays from the next sentence once it is on", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();
        const tile = () => [...page.tiles.values()][0];

        tile()?.listening?.onChange(false);
        await Promise.resolve();
        await Promise.resolve();
        const whileOff = [tileActivities(page), page.players.created[0]?.muted];
        tile()?.listening?.onChange(true);
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "second"));
        await page.players.created[0]?.sounds[1]?.start();

        expect([whileOff, page.players.created[0]?.muted, tileActivities(page)]).toEqual([
            [[AGENT_ACTIVITY.PRESENT], true],
            false,
            [AGENT_ACTIVITY.SPEAKING],
        ]);
    });

    it("CALL-79: mutes one agent only", async () => {
        const page = await pageWithAgents("a1", "a2");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a2", "second"));

        [...page.tiles.values()][0]?.listening?.onChange(false);

        expect([listeningOf(page), page.players.created.map((player) => player.muted)]).toEqual([
            [false, true],
            [true, false],
        ]);
    });

    it("CALL-79: starts on again when the agent is announced anew to a new seat", async () => {
        const page = await pageWithAgents("a1");
        [...page.tiles.values()][0]?.listening?.onChange(false);

        page.seat(false, SIGNAL_TYPE.EXISTING_PARTICIPANTS);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a1"));

        expect(listeningOf(page)).toEqual([true]);
    });
});
