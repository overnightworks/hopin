import { describe, expect, it } from "vitest";
import { fakePlayers } from "./player.fixture.ts";
import { createAgentVoices } from "./voice.ts";
import { VOICE_PATH } from "./wire.ts";

function voices() {
    const players = fakePlayers();
    const reported: [string, boolean][] = [];
    const agentVoices = createAgentVoices(players.createPlayer, (agentId, sentence) => {
        reported.push([agentId, sentence !== null]);
    });
    return { players, reported, agentVoices };
}

describe("an agent's voices", () => {
    it("plays an agent's voice from the route of its id through a player of its own", () => {
        const { players, agentVoices } = voices();

        agentVoices.play("a1", "first");

        expect(players.created.map((player) => player.sounds.map(({ sound }) => sound))).toEqual([
            [`${VOICE_PATH}/first`],
        ]);
    });

    it("hands an agent's voices to its player in the order they arrive", () => {
        const { players, agentVoices } = voices();

        agentVoices.play("a1", "first");
        agentVoices.play("a1", "second");

        expect(players.created.map((player) => player.sounds.map(({ sound }) => sound))).toEqual([
            [`${VOICE_PATH}/first`, `${VOICE_PATH}/second`],
        ]);
    });

    it("gives each agent a player of its own", () => {
        const { players, agentVoices } = voices();

        agentVoices.play("a1", "first");
        agentVoices.play("a2", "second");

        expect(players.created.map((player) => player.sounds.map(({ sound }) => sound))).toEqual([
            [`${VOICE_PATH}/first`],
            [`${VOICE_PATH}/second`],
        ]);
    });

    it("creates the player of an agent only when its first voice comes", () => {
        const { players } = voices();

        expect(players.created).toEqual([]);
    });
});

describe("whether an agent is speaking", () => {
    it("reports the agent speaking from the start of its sound to the end of it", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        const [sound] = players.created[0]?.sounds ?? [];

        await sound?.start();
        const whilePlaying = [...reported];
        await sound?.end({ kind: "played", playedSeconds: 1 });

        expect([whilePlaying, reported]).toEqual([
            [["a1", true]],
            [
                ["a1", true],
                ["a1", false],
            ],
        ]);
    });

    it("reports the agent silent where a sound that streams in breaks off", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        const [sound] = players.created[0]?.sounds ?? [];

        await sound?.start();
        await sound?.end({ kind: "failed" });

        expect(reported).toEqual([
            ["a1", true],
            ["a1", false],
        ]);
    });

    it("starts the agent's next sound after a sound that broke off, which then plays", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a1", "second");
        const [first, second] = players.created[0]?.sounds ?? [];
        await first?.start();

        await first?.end({ kind: "failed" });
        await second?.start();

        expect(reported).toEqual([
            ["a1", true],
            ["a1", false],
            ["a1", true],
        ]);
    });

    it("reports nothing for a sound that ends without having started", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");

        await players.created[0]?.sounds[0]?.end({ kind: "refused" });

        expect(reported).toEqual([]);
    });

    it("reports the agent speaking at each start and never silent while a later sound starts as the one before ends", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a1", "second");
        const [first, second] = players.created[0]?.sounds ?? [];
        await first?.start();

        const next = second?.start();
        await first?.end({ kind: "played", playedSeconds: 1 });
        await next;

        expect(reported).toEqual([
            ["a1", true],
            ["a1", true],
        ]);
    });

    it("reports each agent on its own while two agents' sounds overlap", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a2", "second");
        const [first] = players.created[0]?.sounds ?? [];
        const [second] = players.created[1]?.sounds ?? [];

        await first?.start();
        await second?.start();
        await first?.end({ kind: "played", playedSeconds: 1 });

        expect(reported).toEqual([
            ["a1", true],
            ["a2", true],
            ["a1", false],
        ]);
    });
});

describe("stopping an agent's voice", () => {
    it("ends the playing sound and the waiting ones of the agent and reports it silent", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a1", "second");
        await players.created[0]?.sounds[0]?.start();

        agentVoices.stop("a1");
        await Promise.resolve();
        await Promise.resolve();

        expect(reported).toEqual([
            ["a1", true],
            ["a1", false],
        ]);
    });

    it("stops only the player of the agent it names", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a2", "second");
        await players.created[0]?.sounds[0]?.start();
        await players.created[1]?.sounds[0]?.start();

        agentVoices.stop("a1");
        await Promise.resolve();
        await Promise.resolve();

        expect(reported).toEqual([
            ["a1", true],
            ["a2", true],
            ["a1", false],
        ]);
    });

    it("plays the voices that arrive after the stop", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        await players.created[0]?.sounds[0]?.start();
        agentVoices.stop("a1");

        agentVoices.play("a1", "later");
        await players.created[0]?.sounds[1]?.start();

        expect(reported).toEqual([
            ["a1", true],
            ["a1", false],
            ["a1", true],
        ]);
    });

    it("stops nothing for an agent that never spoke", () => {
        const { players, agentVoices } = voices();

        agentVoices.stop("a1");

        expect(players.created).toEqual([]);
    });
});

describe("holding an agent's voice", () => {
    async function speakingAgent() {
        const scene = voices();
        scene.agentVoices.play("a1", "first");
        const [sound] = scene.players.created[0]?.sounds ?? [];
        await sound?.start();
        return { ...scene, sound };
    }

    it("reports the agent silent at the pause and speaking again at the resume while the same sound plays on", async () => {
        const { players, reported, agentVoices, sound } = await speakingAgent();

        agentVoices.pause("a1");
        const whilePaused = [...reported];
        agentVoices.resume("a1");
        await Promise.resolve();
        await Promise.resolve();
        await sound?.end({ kind: "played", playedSeconds: 1 });

        expect([players.created[0]?.paused, whilePaused, reported]).toEqual([
            false,
            [
                ["a1", true],
                ["a1", false],
            ],
            [
                ["a1", true],
                ["a1", false],
                ["a1", true],
                ["a1", false],
            ],
        ]);
    });

    it("keeps a sound that arrives during the pause waiting, and reports the agent speaking only once it starts after the resume", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.pause("a1");
        agentVoices.play("a1", "first");
        const [sound] = players.created[0]?.sounds ?? [];

        await sound?.start();
        const whilePaused = [...reported];
        agentVoices.resume("a1");
        await Promise.resolve();
        await Promise.resolve();

        expect([whilePaused, reported]).toEqual([[], [["a1", true]]]);
    });

    it("does not report the agent speaking for a sound that starts inside the pause's ramp until the resume", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        const [sound] = players.created[0]?.sounds ?? [];

        agentVoices.pause("a1");
        await sound?.start({ inRamp: true });
        const whilePaused = [...reported];
        agentVoices.resume("a1");

        expect([whilePaused, reported]).toEqual([[], [["a1", true]]]);
    });

    it("ends the sound of a stop that comes during the pause and leaves the player ready, so the next voice plays", async () => {
        const { players, reported, agentVoices } = await speakingAgent();
        agentVoices.pause("a1");

        agentVoices.stop("a1");
        await Promise.resolve();
        await Promise.resolve();
        agentVoices.play("a1", "later");
        await players.created[0]?.sounds[1]?.start();

        expect([players.created[0]?.paused, reported]).toEqual([
            false,
            [
                ["a1", true],
                ["a1", false],
                ["a1", true],
            ],
        ]);
    });

    it("leaves the player of an agent whose hold had no sound ready for the next voice after a stop", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.pause("a1");

        agentVoices.stop("a1");
        agentVoices.play("a1", "later");
        await players.created[0]?.sounds[0]?.start();

        expect([players.created[0]?.paused, reported]).toEqual([false, [["a1", true]]]);
    });

    it("pauses only the player of the agent it names", () => {
        const { players, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a2", "second");

        agentVoices.pause("a1");

        expect(players.created.map((player) => player.paused)).toEqual([true, false]);
    });

    it("resumes nothing for an agent that never spoke or was never held", () => {
        const { players, agentVoices } = voices();

        agentVoices.resume("a1");

        expect(players.created).toEqual([]);
    });
});

describe("closing an agent's player", () => {
    it("closes only the player of the agent that leaves, and the other keeps playing", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a2", "second");
        await players.created[1]?.sounds[0]?.start();

        agentVoices.close("a1");
        await players.created[1]?.sounds[0]?.end({ kind: "played", playedSeconds: 1 });

        expect([players.created.map((player) => player.closed), reported]).toEqual([
            [true, false],
            [
                ["a2", true],
                ["a2", false],
            ],
        ]);
    });

    it("reports nothing of a sound that ends because its player was closed", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        await players.created[0]?.sounds[0]?.start();

        agentVoices.close("a1");
        await players.created[0]?.sounds[0]?.end({ kind: "stopped", playedSeconds: 1 });

        expect(reported).toEqual([["a1", true]]);
    });

    it("reports nothing of a sound that starts as its player closes", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");

        const starting = players.created[0]?.sounds[0]?.start();
        agentVoices.close("a1");
        await starting;

        expect(reported).toEqual([]);
    });

    it("gives an agent that speaks again after its player closed a new one", () => {
        const { players, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.close("a1");

        agentVoices.play("a1", "second");

        expect(players.created.map((player) => [player.closed, player.sounds.length])).toEqual([
            [true, 1],
            [false, 1],
        ]);
    });

    it("closes nothing for an agent that never spoke", () => {
        const { players, agentVoices } = voices();

        agentVoices.close("a1");

        expect(players.created).toEqual([]);
    });
});

describe("muting an agent's voice", () => {
    it("ends the agent's playing sound at once, reports it silent and plays none of its later sounds", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        await players.created[0]?.sounds[0]?.start();

        agentVoices.setMuted("a1", true);
        await Promise.resolve();
        await Promise.resolve();
        agentVoices.play("a1", "second");
        await players.created[0]?.sounds[1]?.start();

        expect([players.created[0]?.muted, reported]).toEqual([
            true,
            [
                ["a1", true],
                ["a1", false],
            ],
        ]);
    });

    it("plays the agent's sounds that arrive after it is unmuted", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.setMuted("a1", true);
        agentVoices.setMuted("a1", false);
        agentVoices.play("a1", "second");

        await players.created[0]?.sounds[1]?.start();

        expect([players.created[0]?.muted, reported]).toEqual([false, [["a1", true]]]);
    });

    it("leaves the other agents' players unmuted", () => {
        const { players, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a2", "second");

        agentVoices.setMuted("a1", true);

        expect(players.created.map((player) => player.muted)).toEqual([true, false]);
    });

    it("mutes the player of an agent that has not spoken yet when it is made", () => {
        const { players, agentVoices } = voices();
        agentVoices.setMuted("a1", true);

        agentVoices.play("a1", "first");

        expect(players.created.map((player) => player.muted)).toEqual([true]);
    });

    it("shows the agent speaking once it is unmuted after a hold the mute cleared", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        await players.created[0]?.sounds[0]?.start();
        agentVoices.pause("a1");

        agentVoices.setMuted("a1", true);
        await Promise.resolve();
        await Promise.resolve();
        agentVoices.setMuted("a1", false);
        agentVoices.play("a1", "second");
        await players.created[0]?.sounds[1]?.start();

        expect(reported.at(-1)).toEqual(["a1", true]);
    });

    it("tells whether an agent is muted, and forgets the mute when its player closes", () => {
        const { agentVoices } = voices();
        agentVoices.setMuted("a1", true);
        const whileMuted = agentVoices.isMuted("a1");

        agentVoices.close("a1");

        expect([whileMuted, agentVoices.isMuted("a1")]).toEqual([true, false]);
    });
});
