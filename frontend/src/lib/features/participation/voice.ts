import type { Playback, Player } from "overnightworks-speech-browser";
import type { AgentSentence } from "#lib/call/feature-port.ts";
import { VOICE_PATH, type FIGURE_MOOD } from "./wire.ts";

export type PlayerFactory = () => Player;

/** What the person's browser plays of the agents' sentences: each agent's voices go to a player of its own, in the order they arrive, and the agent counts as speaking from the start of one of its sounds to its end. A sound plays while its route's body still arrives, and one whose body breaks off ends where it broke. */
export interface AgentVoices {
    /** Queues the voice of the route's id, spoken with `mood`, behind the agent's earlier ones; the agent's first voice creates its player. */
    play(agentId: string, voiceId: string, mood?: FIGURE_MOOD): void;
    /** Ends the agent's playing sound at once and drops its waiting ones, and reports the agent silent; later sounds play. */
    stop(agentId: string): void;
    /** Closes the agent's player, which ends every sound of it, and reports nothing more for the agent. */
    close(agentId: string): void;
}

interface AgentPlayer {
    readonly player: Player;
    playing: number;
}

/** `createPlayer` makes one player for each agent, because a player has one audio node; `report` is told, at the start of each of the agent's sounds, the sentence it speaks: the player's node and the sound's mood, and null once the agent is silent. */
export function createAgentVoices(
    createPlayer: PlayerFactory,
    report: (agentId: string, sentence: AgentSentence | null) => void,
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
    async function follow(agentId: string, held: AgentPlayer, playback: Playback, mood: FIGURE_MOOD | undefined) {
        if ((await playback.started).kind !== "playing") return;
        const isOpen = () => players.get(agentId) === held;
        if (!isOpen()) return;
        held.playing += 1;
        report(agentId, { voice: held.player.tap, mood });
        await playback.ended;
        if (!isOpen()) return;
        held.playing -= 1;
        if (held.playing === 0) report(agentId, null);
    }

    return {
        play(agentId, voiceId, mood) {
            const held = playerOf(agentId);
            void follow(agentId, held, held.player.play(`${VOICE_PATH}/${voiceId}`), mood);
        },
        stop(agentId) {
            players.get(agentId)?.player.stop();
        },
        close(agentId) {
            players.get(agentId)?.player.close();
            players.delete(agentId);
        },
    };
}
