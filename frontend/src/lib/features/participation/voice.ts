import type { Playback, Player } from "overnightworks-speech-browser";
import type { AgentSentence } from "#lib/call/feature-port.ts";
import { VOICE_PATH, type FIGURE_MOOD } from "./wire.ts";

export type PlayerFactory = () => Player;

/** The sound an agent's player was in when it paused: its route's id and the seconds of it the player had played. */
export interface HeldPlace {
    readonly voiceId: string;
    readonly playedSeconds: number;
}

/** What the person's browser plays of the agents' sentences: each agent's voices go to a player of its own, in the order they arrive, and the agent counts as speaking from the start of one of its sounds to its end, except while its player is paused. A sound plays while its route's body still arrives, and one whose body breaks off ends where it broke. */
export interface AgentVoices {
    /** Queues the voice of the route's id, spoken with `mood`, behind the agent's earlier ones; the agent's first voice creates its player. */
    play(agentId: string, voiceId: string, mood?: FIGURE_MOOD): void;
    /** Holds the agent's player where its sound is, which also holds the sounds that arrive meanwhile, and reports the agent silent until `resume`. Answers where the sound that started last stood at the pause, none when no sound of the agent started here or the agent was stopped or muted since. */
    pause(agentId: string): HeldPlace | null;
    /** Lets the agent's player go on from where it paused, and reports the agent speaking again when a sound plays. */
    resume(agentId: string): void;
    /** Ends the agent's playing sound at once and drops its waiting ones, ends a pause, and reports the agent silent; later sounds play. */
    stop(agentId: string): void;
    /** Mutes or unmutes the agent in this browser alone: muted, its playing sound ends at once, its waiting ones and every later one never play and are not fetched, and the agent is reported silent; unmuted, the agent's next sounds play. The choice lasts until `close`. */
    setMuted(agentId: string, muted: boolean): void;
    isMuted(agentId: string): boolean;
    /** Closes the agent's player, which ends every sound of it, and reports nothing more for the agent. */
    close(agentId: string): void;
}

interface AgentPlayer {
    readonly player: Player;
    /** False once the agent's player is closed. */
    open: boolean;
    playing: number;
    paused: boolean;
    /** The sentence of the sound that started last, while a sound plays. */
    sentence: AgentSentence | null;
    /** What was last reported: the sentence while one is heard, else null. */
    reported: AgentSentence | null;
    /** The sound that started last and how far it has played, until the agent is stopped or muted. */
    current: { readonly voiceId: string; readonly playback: Playback } | null;
}

// The agent is heard speaking while a sound plays and its player is not paused; a sound that starts inside a pause's ramp is heard only once the player resumes.
function show(report: SentenceReport, agentId: string, held: AgentPlayer) {
    const heard = held.paused || held.playing === 0 ? null : held.sentence;
    if (heard === held.reported) return;
    held.reported = heard;
    report(agentId, heard);
}

// A refused or failed sound never starts, so it neither makes the agent speak nor shows an error.
async function follow(report: SentenceReport, agentId: string, held: AgentPlayer, sound: Sound) {
    const { playback, voiceId, mood } = sound;
    const isOpen = () => held.open;
    if ((await playback.started).kind !== "playing" || !isOpen()) return;
    held.playing += 1;
    held.sentence = { voice: held.player.tap, mood };
    held.current = { voiceId, playback };
    show(report, agentId, held);
    await playback.ended;
    if (!isOpen()) return;
    held.playing -= 1;
    show(report, agentId, held);
}

type SentenceReport = (agentId: string, sentence: AgentSentence | null) => void;

interface Sound {
    readonly playback: Playback;
    readonly voiceId: string;
    readonly mood: FIGURE_MOOD | undefined;
}

/** `createPlayer` makes one player for each agent, because a player has one audio node; `report` is told, at the start of each of the agent's sounds, the sentence it speaks: the player's node and the sound's mood, and null once the agent is silent. */
export function createAgentVoices(createPlayer: PlayerFactory, report: SentenceReport): AgentVoices {
    const players = new Map<string, AgentPlayer>();
    // The agents this browser muted; an agent's player is made only at its first voice, so the choice is kept here.
    const muted = new Set<string>();

    function playerOf(agentId: string) {
        let held = players.get(agentId);
        if (held === undefined) {
            held = {
                player: createPlayer(),
                open: true,
                playing: 0,
                paused: false,
                sentence: null,
                reported: null,
                current: null,
            };
            held.player.setMuted(muted.has(agentId));
            players.set(agentId, held);
        }
        return held;
    }

    return {
        play(agentId, voiceId, mood) {
            const held = playerOf(agentId);
            const playback = held.player.play(`${VOICE_PATH}/${voiceId}`);
            void follow(report, agentId, held, { playback, voiceId, mood });
        },
        pause(agentId) {
            const held = playerOf(agentId);
            held.player.pause();
            held.paused = true;
            show(report, agentId, held);
            return (
                held.current && { voiceId: held.current.voiceId, playedSeconds: held.current.playback.playedSeconds() }
            );
        },
        resume(agentId) {
            const held = players.get(agentId);
            if (held === undefined) return;
            held.player.resume();
            held.paused = false;
            show(report, agentId, held);
        },
        // A stop leaves a pause standing, so the player resumes right after it: it is then silent and ready.
        stop(agentId) {
            const held = players.get(agentId);
            if (held === undefined) return;
            held.player.stop();
            held.player.resume();
            held.paused = false;
            held.current = null;
        },
        // Muting clears the player's pause, so the agent is no longer held here.
        setMuted(agentId, isMuted) {
            if (isMuted) muted.add(agentId);
            else muted.delete(agentId);
            const held = players.get(agentId);
            if (held === undefined) return;
            held.player.setMuted(isMuted);
            if (isMuted) [held.paused, held.current] = [false, null];
            show(report, agentId, held);
        },
        isMuted: (agentId) => muted.has(agentId),
        close(agentId) {
            muted.delete(agentId);
            const held = players.get(agentId);
            held?.player.close();
            if (held) held.open = false;
            players.delete(agentId);
        },
    };
}
