import type { Playback, Player } from "overnightworks-speech-browser";
import { VOICE_PATH } from "./wire.ts";

export type PlayerFactory = () => Player;

/** What the person's browser plays of the agents' sentences: each agent's voices go to a player of its own, in the order they arrive, and the agent counts as speaking from the start of one of its sounds to its end. */
export interface AgentVoices {
    /** Queues the voice of the route's id behind the agent's earlier ones; the agent's first voice creates its player. */
    play(agentId: string, voiceId: string): void;
    /** Closes the agent's player, which ends every sound of it, and reports nothing more for the agent. */
    close(agentId: string): void;
}

interface AgentPlayer {
    readonly player: Player;
    playing: number;
}

/** `createPlayer` makes one player for each agent, because a player has one audio node; `report` is told each change of whether the agent is speaking. */
export function createAgentVoices(
    createPlayer: PlayerFactory,
    report: (agentId: string, speaking: boolean) => void,
): AgentVoices {
    const players = new Map<string, AgentPlayer>();

    function playerOf(agentId: string) {
        let held = players.get(agentId);
        if (held === undefined) {
            held = { player: createPlayer(), playing: 0 };
            players.set(agentId, held);
        }
        return held;
    }

    // A refused or failed sound never starts, so it neither makes the agent speak nor shows an error.
    async function follow(agentId: string, held: AgentPlayer, playback: Playback) {
        if ((await playback.started) !== "playing") return;
        const isOpen = () => players.get(agentId) === held;
        if (!isOpen()) return;
        held.playing += 1;
        if (held.playing === 1) report(agentId, true);
        await playback.ended;
        if (!isOpen()) return;
        held.playing -= 1;
        if (held.playing === 0) report(agentId, false);
    }

    return {
        play(agentId, voiceId) {
            const held = playerOf(agentId);
            void follow(agentId, held, held.player.play(`${VOICE_PATH}/${voiceId}`));
        },
        close(agentId) {
            players.get(agentId)?.player.close();
            players.delete(agentId);
        },
    };
}
