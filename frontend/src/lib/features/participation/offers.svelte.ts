import type { AgentControlOutcome, AgentControlRefusal, AgentRow } from "overnightworks-design";
import { AGENT_REFUSAL, INVITATION_OUTCOME } from "./wire.ts";

/** What the page shows of the agents a seat may call: whether the control shows, its rows and the refusal of the last call. */
export interface AgentOffersView {
    shown: boolean;
    rows: AgentRow[];
    refusal: AgentControlRefusal | undefined;
}

export interface Offer {
    agent_key: string;
    name: string;
    pending: boolean;
}

export function createAgentOffers(): AgentOffersView {
    const view: AgentOffersView = $state({ shown: false, rows: [], refusal: undefined });
    return view;
}

/** The rows of an offers frame; an agent whose last invitation ended shows that outcome until it is invited again. */
export function rowsOf(offers: Offer[], endings: ReadonlyMap<string, AgentControlOutcome>): AgentRow[] {
    return offers.map((offer): AgentRow => {
        const outcome = endings.get(offer.agent_key);
        if (offer.pending) return { key: offer.agent_key, name: offer.name, state: "inviting" };
        if (outcome !== undefined) return { key: offer.agent_key, name: offer.name, state: "ended", outcome };
        return { key: offer.agent_key, name: offer.name, state: "available" };
    });
}

const OUTCOME_OF_ENDING: Record<INVITATION_OUTCOME, AgentControlOutcome> = {
    [INVITATION_OUTCOME.NO_ANSWER]: "no-answer",
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
    [AGENT_REFUSAL.GONE]: "agent-unavailable",
    [AGENT_REFUSAL.TWO_SEATS]: "agent-unavailable",
    [AGENT_REFUSAL.UNAVAILABLE]: "try-again",
};

/** The kit's reason for a refusal of an add; any reason the page does not know reads as a failed try. */
export function refusalOf(reason: string): AgentControlRefusal {
    return REFUSAL_OF_REASON[reason] ?? "try-again";
}
