import { describe, expect, it } from "vitest";
import { fakePlayers } from "./player.fixture.ts";
import { createAgentVoices } from "./voice.ts";
import { VOICE_PATH } from "./wire.ts";

function voices() {
    const players = fakePlayers();
    const reported: [string, boolean][] = [];
    const agentVoices = createAgentVoices(players.createPlayer, (agentId, speaking) => {
        reported.push([agentId, speaking]);
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

    it("reports nothing for a sound that ends without having started", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");

        await players.created[0]?.sounds[0]?.end({ kind: "refused" });

        expect(reported).toEqual([]);
    });

    it("keeps reporting the agent speaking while a later sound starts as the one before ends", async () => {
        const { players, reported, agentVoices } = voices();
        agentVoices.play("a1", "first");
        agentVoices.play("a1", "second");
        const [first, second] = players.created[0]?.sounds ?? [];
        await first?.start();

        const next = second?.start();
        await first?.end({ kind: "played", playedSeconds: 1 });
        await next;

        expect(reported).toEqual([["a1", true]]);
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
