import { flushSync, mount } from "svelte";
import { AGENT_ACTIVITY, type FeatureCore } from "#lib/call/feature-port.ts";
import { featureView } from "#lib/ui/chat.svelte.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import "./participation.css";
import AgentLine from "./AgentLine.svelte";
import AgentOffers from "./AgentOffers.svelte";
import { createAgentOffers, refusalOf, rowsOf, type Offer } from "./offers.svelte.ts";
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
    figure?: string;
}

interface AgentLeftFrame {
    agent_id: string;
}

interface AgentOffersFrame {
    shown: boolean;
    offers: Offer[];
}

interface AgentLineFrame {
    name: string;
    text: string;
}

interface AgentRefusedFrame {
    reason: AGENT_REFUSAL;
}

const NOT_REMOVED_TEXT = "Only the person who brought the agent or the host can remove it.";

function tileKey(agentId: string) {
    return `agent-${agentId}`;
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
                figure: frame.figure,
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
    offers.tabIndex = -1;
    const offersView = createAgentOffers();
    mount(AgentOffers, {
        target: offers,
        props: {
            view: offersView,
            onCall: (agentKey: string) => {
                offersView.refusal = undefined;
                core.send({ type: PARTICIPATION_SIGNAL.AGENT_ADD, agent_key: agentKey });
            },
        },
    });
    core.addMoreItems(offers);

    function clearOffers() {
        offersView.shown = false;
        offersView.rows = [];
        offersView.refusal = undefined;
    }

    // The kit replaces the button that had focus when its row turns inviting, and focus would fall to the page.
    function showOffers(frame: AgentOffersFrame) {
        const hadFocus = offers.contains(document.activeElement);
        offersView.shown = frame.shown;
        offersView.rows = rowsOf(frame.offers);
        flushSync();
        if (hadFocus && !offers.contains(document.activeElement)) offers.focus();
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
        clearOffers();
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, seated);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, seated);
    core.on(PARTICIPATION_SIGNAL.AGENT_JOINED, show);
    core.on(PARTICIPATION_SIGNAL.AGENT_LEFT, (frame: AgentLeftFrame) => {
        hide(frame.agent_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_OFFERS, showOffers);
    core.on(PARTICIPATION_SIGNAL.AGENT_LINE, (frame: AgentLineFrame) => {
        core.receiveChatLine(featureView(AgentLine, { name: frame.name, text: frame.text }));
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_REFUSED, (frame: AgentRefusedFrame) => {
        if (frame.reason === AGENT_REFUSAL.NOT_SPONSOR_OR_HOST) {
            core.showToast(NOT_REMOVED_TEXT, core.toastKind.LOCKED, null);
        } else {
            offersView.refusal = refusalOf(frame.reason);
        }
    });
    core.onCallEnded(() => {
        for (const agentId of shown) hide(agentId);
        clearOffers();
    });
    return Promise.resolve();
}
