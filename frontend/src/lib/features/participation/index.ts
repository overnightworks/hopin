import { loadKitWords } from "overnightworks-design";
import { startCapture } from "overnightworks-speech-browser";
import { flushSync, mount } from "svelte";
import { AGENT_ACTIVITY, type FeatureCore } from "#lib/call/feature-port.ts";
import { featureView } from "#lib/ui/chat.svelte.ts";
import { words } from "#lib/words/de.ts";
import { sourceLanguage } from "#lib/words/index.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import "./participation.css";
import AgentLine from "./AgentLine.svelte";
import AgentOffers from "./AgentOffers.svelte";
import { createHearingCapture } from "./hearing.ts";
import { createAgentOffers, refusalOf, rowsOf, type Offer } from "./offers.svelte.ts";
import { AGENT_REFUSAL, HEARING_PATH, PARTICIPATION_SIGNAL } from "./wire.ts";

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

interface AgentHearingFrame {
    agent_id: string;
    on: boolean;
}

interface ShownAgent {
    readonly joined: AgentJoinedFrame;
    // The server's record of the sponsor's switch; absent until its frame comes, which only the sponsor gets.
    readonly hearing?: boolean;
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

const NOT_REMOVED_TEXT = words["agent.notRemovedToast"];

function tileKey(agentId: string) {
    return `agent-${agentId}`;
}

export async function install(core: FeatureCore): Promise<void> {
    const kitWords = await loadKitWords(sourceLanguage);
    let seatId = "";
    let isHost = false;
    const shown = new Map<string, ShownAgent>();
    const capture = createHearingCapture(
        () => `${location.protocol.replace("http", "ws")}//${location.host}${HEARING_PATH}/${seatId}`,
        startCapture,
    );

    // The server's record decides; `except` leaves out an agent whose switch the person has just pressed off.
    function agentHears(except?: string) {
        return [...shown.values()].some((agent) => agent.hearing === true && agent.joined.agent_id !== except);
    }

    function mayRemove(frame: AgentJoinedFrame) {
        return isHost || frame.sponsor_id === seatId;
    }

    function draw({ joined: frame, hearing }: ShownAgent) {
        core.showAgentTile(
            {
                name: frame.name,
                activity: AGENT_ACTIVITY.PRESENT,
                sponsor: frame.sponsor,
                figure: frame.figure,
                hearing:
                    hearing === undefined
                        ? undefined
                        : {
                              on: hearing,
                              onChange: (on: boolean) => {
                                  if (!on) capture.setHeard(agentHears(frame.agent_id));
                                  core.send({ type: PARTICIPATION_SIGNAL.AGENT_HEARING, agent_id: frame.agent_id, on });
                              },
                          },
                onRemove: mayRemove(frame)
                    ? () => {
                          core.send({ type: PARTICIPATION_SIGNAL.AGENT_REMOVE, agent_id: frame.agent_id });
                      }
                    : undefined,
            },
            tileKey(frame.agent_id),
        );
    }

    function show(frame: AgentJoinedFrame) {
        const agent: ShownAgent = { joined: frame, hearing: shown.get(frame.agent_id)?.hearing };
        shown.set(frame.agent_id, agent);
        draw(agent);
    }

    function showHearing(frame: AgentHearingFrame) {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        const recorded = { ...agent, hearing: frame.on };
        shown.set(frame.agent_id, recorded);
        draw(recorded);
        capture.setHeard(agentHears());
    }

    const offers = document.createElement("div");
    offers.id = "agentOffers";
    offers.tabIndex = -1;
    const offersView = createAgentOffers();
    mount(AgentOffers, {
        target: offers,
        props: {
            view: offersView,
            words: kitWords,
            onCall: (agentKey: string) => {
                clearRefusal();
                core.send({ type: PARTICIPATION_SIGNAL.AGENT_ADD, agent_key: agentKey });
            },
        },
    });
    core.addMoreItems(offers);
    // A refusal leaves with the next offers frame (the server sends an add's own offers frame before its refusal) and
    // when the person closes More, for the server sends no frame when the person only lost the right.
    offers.closest("dialog")?.addEventListener("close", clearRefusal);

    function clearRefusal() {
        offersView.refusal = undefined;
    }

    function clearOffers() {
        offersView.shown = false;
        offersView.rows = [];
        clearRefusal();
    }

    // The kit replaces the button that had focus when its row turns inviting, and focus would fall to the page.
    function showOffers(frame: AgentOffersFrame) {
        const hadFocus = offers.contains(document.activeElement);
        clearRefusal();
        offersView.shown = frame.shown;
        offersView.rows = rowsOf(frame.offers);
        flushSync();
        if (hadFocus && !offers.contains(document.activeElement)) offers.focus();
    }

    function hide(agentId: string) {
        shown.delete(agentId);
        core.showAgentTile(null, tileKey(agentId));
        capture.setHeard(agentHears());
    }

    const seated = (frame: SeatFrame) => {
        seatId = frame.participant_id;
        isHost = frame.is_host;
        // The server announces every agent still in the room to a new seat; one it does not announce has left.
        for (const agentId of shown.keys()) hide(agentId);
        clearOffers();
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, seated);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, seated);
    core.on(PARTICIPATION_SIGNAL.AGENT_JOINED, show);
    core.on(PARTICIPATION_SIGNAL.AGENT_LEFT, (frame: AgentLeftFrame) => {
        hide(frame.agent_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_HEARING, showHearing);
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
    core.onMicrophone((stream) => {
        capture.setStream(stream);
    });
    core.onCallEnded(() => {
        for (const agentId of shown.keys()) hide(agentId);
        clearOffers();
    });
}
