import type { AgentControlOutcome, AgentControlRefusal, AgentRow, TurnOutcome } from "overnightworks-design";
import { AGENT_REFUSAL, INVITATION_OUTCOME, TURN_OUTCOME, type Offer } from "./wire.ts";

/** What the page shows of the agents a seat may call: whether the control shows, whether no place is free for an agent,
 * its rows and the refusal of the last call. */
export interface AgentOffersView {
    shown: boolean;
    full: boolean;
    rows: AgentRow[];
    refusal: AgentControlRefusal | undefined;
}

export function createAgentOffers(): AgentOffersView {
    const view: AgentOffersView = $state({ shown: false, full: false, rows: [], refusal: undefined });
    return view;
}

/** How an agent's last invitation ended, with the name its row shows when the agent is no longer offered. */
export interface Ending {
    name: string;
    outcome: AgentControlOutcome;
}

const TURN_STATE_OF_OUTCOME: Record<TURN_OUTCOME, TurnOutcome> = {
    [TURN_OUTCOME.NOT_ENABLED]: "not_enabled",
    [TURN_OUTCOME.BUSY]: "busy",
    [TURN_OUTCOME.BUDGET_SPENT]: "budget_spent",
    [TURN_OUTCOME.UNAVAILABLE]: "unavailable",
};

/** The row of an agent that is in the call, with the outcome it stated for its stopped turns, if any. */
export function hereRowOf(agentId: string, name: string, outcome: TURN_OUTCOME | undefined): AgentRow {
    return {
        key: agentId,
        name,
        state: "here",
        turnState: outcome === undefined ? undefined : TURN_STATE_OF_OUTCOME[outcome],
    };
}

/** The rows of the control: the agents in the call first, then those of an offers frame; an agent whose last invitation
 * ended shows that outcome until it is invited again, and an ended agent that is not offered keeps its ended row after
 * the offered ones. */
export function rowsOf(here: readonly AgentRow[], offers: Offer[], endings: ReadonlyMap<string, Ending>): AgentRow[] {
    const offeredRows = offers.map((offer): AgentRow => {
        const ending = endings.get(offer.agent_key);
        if (offer.pending) return { key: offer.agent_key, name: offer.name, state: "inviting" };
        if (ending !== undefined)
            return { key: offer.agent_key, name: offer.name, state: "ended", outcome: ending.outcome };
        return { key: offer.agent_key, name: offer.name, state: "available" };
    });
    const unlistedRows = [...endings]
        .filter(([key]) => !offers.some((offer) => offer.agent_key === key))
        .map(([key, { name, outcome }]): AgentRow => ({ key, name, state: "ended", outcome }));
    return [...here, ...offeredRows, ...unlistedRows];
}

/** Keeps the ended rows an offers frame leaves standing: an owner-away row outlives the agent's absence from the list
 * and goes when the owner is back; any other goes when the agent is invited again or no longer listed. */
export function keepStandingEndings(endings: Map<string, Ending>, offers: readonly Offer[]): void {
    for (const [key, { outcome }] of endings) {
        const offer = offers.find((candidate) => candidate.agent_key === key);
        const stays = outcome === "owner-absent" ? offer === undefined : offer !== undefined && !offer.pending;
        if (!stays) endings.delete(key);
    }
}

/** Drops the ended row of an agent the seat is no longer offered once its outcome has run out; the row of an offered
 * agent stays, and the control then shows its call key. */
export function dropLapsedEnding(endings: Map<string, Ending>, offers: readonly Offer[], key: string): void {
    if (!offers.some((offer) => offer.agent_key === key)) endings.delete(key);
}

const OUTCOME_OF_ENDING: Record<INVITATION_OUTCOME, AgentControlOutcome> = {
    [INVITATION_OUTCOME.NO_ANSWER]: "no-answer",
    [INVITATION_OUTCOME.BUSY]: "busy",
    [INVITATION_OUTCOME.OWNER_AWAY]: "owner-absent",
};

/** The kit's outcome for the way an invitation ended. */
export function outcomeOf(ending: INVITATION_OUTCOME): AgentControlOutcome {
    return OUTCOME_OF_ENDING[ending];
}

const REFUSAL_OF_REASON: Partial<Record<string, AgentControlRefusal>> = {
    [AGENT_REFUSAL.NOT_ADDER]: "not-allowed",
    [AGENT_REFUSAL.NO_SEAT]: "owner-absent",
    [AGENT_REFUSAL.SPONSOR_AWAY]: "owner-absent",
    [AGENT_REFUSAL.BUSY]: "place-full",
    [AGENT_REFUSAL.ALREADY_THERE]: "agent-unavailable",
    [AGENT_REFUSAL.NOT_LISTENING]: "agent-unavailable",
    [AGENT_REFUSAL.INVITED_BY_ANOTHER]: "agent-unavailable",
    [AGENT_REFUSAL.GONE]: "agent-unavailable",
    [AGENT_REFUSAL.TWO_SEATS]: "agent-unavailable",
    [AGENT_REFUSAL.UNAVAILABLE]: "try-again",
};

/** The kit's reason for a refusal of an add; any reason the page does not know reads as a failed try. */
export function refusalOf(reason: string): AgentControlRefusal {
    return REFUSAL_OF_REASON[reason] ?? "try-again";
}
