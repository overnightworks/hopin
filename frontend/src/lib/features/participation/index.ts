import { AGENT_ACTIVITY, type FeatureCore } from "#lib/call/feature-port.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { AGENT_REFUSAL, PARTICIPATION_SIGNAL } from "./wire.ts";

interface SeatFrame {
    participant_id: string;
    is_host: boolean;
}

interface AgentJoinedFrame {
    agent_id: string;
    name: string;
    sponsor: string;
    sponsor_id: string;
}

interface AgentLeftFrame {
    agent_id: string;
}

interface Offer {
    agent_key: string;
    name: string;
    pending: boolean;
}

interface AgentOffersFrame {
    offers: Offer[];
}

interface AgentRefusedFrame {
    reason: string;
}

const NOT_ADDED_TEXT = "The agent could not be added.";
const REFUSED_TEXT: Partial<Record<string, string>> = {
    [AGENT_REFUSAL.NOT_SPONSOR_OR_HOST]: "Only the person who brought the agent or the host can remove it.",
    [AGENT_REFUSAL.NOT_ADDER]: "You may not add this agent.",
    [AGENT_REFUSAL.NO_SEAT]: "The agent's owner is not in this call.",
    [AGENT_REFUSAL.TWO_SEATS]: "The agent's owner is in this call twice, so the agent cannot join.",
    [AGENT_REFUSAL.SPONSOR_AWAY]: "The agent's owner is away. Try again when they are back.",
    [AGENT_REFUSAL.ALREADY_THERE]: "The agent is already invited or in the call.",
    [AGENT_REFUSAL.BUSY]: "The call has no room for another agent.",
    [AGENT_REFUSAL.GONE]: "The agent is not available any more.",
    [AGENT_REFUSAL.UNAVAILABLE]: "Your access could not be checked. Try again.",
};

function tileKey(agentId: string) {
    return `agent-${agentId}`;
}

function offerButton(offer: Offer, add: () => void) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mi agent-offer";
    const label = document.createElement("span");
    label.className = "grow";
    label.textContent = offer.pending ? `Inviting ${offer.name}…` : `Add ${offer.name}`;
    button.append(label);
    if (offer.pending) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
    } else {
        button.addEventListener("click", add);
    }
    return button;
}

export function install(core: FeatureCore): Promise<void> {
    let seatId = "";
    let isHost = false;
    const shown = new Set<string>();

    function mayRemove(frame: AgentJoinedFrame) {
        return isHost || frame.sponsor_id === seatId;
    }

    function show(frame: AgentJoinedFrame) {
        shown.add(frame.agent_id);
        core.showAgentTile(
            {
                name: frame.name,
                activity: AGENT_ACTIVITY.PRESENT,
                sponsor: frame.sponsor,
                onRemove: mayRemove(frame)
                    ? () => {
                          core.send({ type: PARTICIPATION_SIGNAL.AGENT_REMOVE, agent_id: frame.agent_id });
                      }
                    : undefined,
            },
            tileKey(frame.agent_id),
        );
    }

    const offers = document.createElement("div");
    offers.id = "agentOffers";
    core.addMoreItems(offers);

    function showOffers(frame: AgentOffersFrame) {
        offers.replaceChildren(
            ...frame.offers.map((offer) =>
                offerButton(offer, () => {
                    core.send({ type: PARTICIPATION_SIGNAL.AGENT_ADD, agent_key: offer.agent_key });
                }),
            ),
        );
    }

    function hide(agentId: string) {
        shown.delete(agentId);
        core.showAgentTile(null, tileKey(agentId));
    }

    const seated = (frame: SeatFrame) => {
        seatId = frame.participant_id;
        isHost = frame.is_host;
        // The server announces every agent still in the room to a new seat; one it does not announce has left.
        for (const agentId of shown) hide(agentId);
        offers.replaceChildren();
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, seated);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, seated);
    core.on(PARTICIPATION_SIGNAL.AGENT_JOINED, show);
    core.on(PARTICIPATION_SIGNAL.AGENT_LEFT, (frame: AgentLeftFrame) => {
        hide(frame.agent_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_OFFERS, showOffers);
    core.on(PARTICIPATION_SIGNAL.AGENT_REFUSED, (frame: AgentRefusedFrame) => {
        core.showToast(REFUSED_TEXT[frame.reason] ?? NOT_ADDED_TEXT, core.toastKind.LOCKED, null);
    });
    core.onCallEnded(() => {
        for (const agentId of shown) hide(agentId);
        offers.replaceChildren();
    });
    return Promise.resolve();
}
