import { loadKitWords, type AgentControlOutcome } from "overnightworks-design";
import { createPlayer as createSpeechPlayer, startCapture } from "overnightworks-speech-browser";
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
import { createLooks } from "./looking.ts";
import { createAgentOffers, outcomeOf, refusalOf, rowsOf, type Offer } from "./offers.svelte.ts";
import { createAgentVoices, type PlayerFactory } from "./voice.ts";
import { AGENT_REFUSAL, HEARING_PATH, INVITATION_OUTCOME, PARTICIPATION_SIGNAL } from "./wire.ts";

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

type AgentSeeingFrame = AgentHearingFrame;

interface AgentLookRequestFrame {
    request_id: string;
}

interface AgentLookedFrame {
    agent_id: string;
}

interface ShownAgent {
    readonly joined: AgentJoinedFrame;
    // The server's record of the sponsor's switches; absent until its frame comes, which only the sponsor gets.
    readonly hearing?: boolean;
    readonly seeing?: boolean;
    // A look of the agent was shown lately.
    readonly looking?: boolean;
}

interface AgentVoiceFrame {
    agent_id: string;
    voice_id: string;
}

interface AgentStopFrame {
    agent_id: string;
}

interface AgentOffersFrame {
    shown: boolean;
    full: boolean;
    offers: Offer[];
}

interface InvitationEndedFrame {
    agent_key: string;
    outcome: INVITATION_OUTCOME;
}

interface AgentLineFrame {
    name: string;
    text: string;
}

interface AgentRefusedFrame {
    reason: AGENT_REFUSAL;
}

// How long the mark of a look stays on the agent's tile.
const LOOK_MARK_MILLISECONDS = 3000;

const NOT_REMOVED_TEXT = words["agent.notRemovedToast"];

function tileKey(agentId: string) {
    return `agent-${agentId}`;
}

export function install(core: FeatureCore): Promise<void> {
    return installWith(core, createSpeechPlayer);
}

/** `createPlayer` makes the player of each agent that speaks. */
export async function installWith(core: FeatureCore, createPlayer: PlayerFactory): Promise<void> {
    const kitWords = await loadKitWords(sourceLanguage);
    let seatId = "";
    let isHost = false;
    const shown = new Map<string, ShownAgent>();
    const capture = createHearingCapture(
        () => `${location.protocol.replace("http", "ws")}//${location.host}${HEARING_PATH}/${seatId}`,
        startCapture,
    );
    const looks = createLooks(
        (bounds) => core.takePicture(bounds),
        (frame) => {
            core.send(frame);
        },
        (frame) => {
            core.sendTransient(frame);
        },
    );
    // Agents whose sound plays now: their tile shows them speaking.
    const speaking = new Set<string>();
    const voices = createAgentVoices(createPlayer, (agentId, isSpeaking) => {
        if (isSpeaking) speaking.add(agentId);
        else speaking.delete(agentId);
        // Only a player that is still open reports, and `hide` closes the player of an agent before it leaves `shown`.
        draw(shown.get(agentId) as ShownAgent);
    });
    // Agents whose switch the person pressed off and whose record has not answered yet: until it does, the capture
    // stays closed, for no sound sent after the press may reach them, whichever other agents still hear the person.
    const pressedOff = new Set<string>();

    function hearingAgents() {
        if (pressedOff.size > 0) return 0;
        return [...shown.values()].filter((agent) => agent.hearing === true).length;
    }

    function mayRemove(frame: AgentJoinedFrame) {
        return isHost || frame.sponsor_id === seatId;
    }

    function draw({ joined: frame, hearing, seeing, looking }: ShownAgent) {
        core.showAgentTile(
            {
                name: frame.name,
                activity: speaking.has(frame.agent_id) ? AGENT_ACTIVITY.SPEAKING : AGENT_ACTIVITY.PRESENT,
                sponsor: frame.sponsor,
                figure: frame.figure,
                hearing:
                    hearing === undefined
                        ? undefined
                        : {
                              on: hearing,
                              onChange: (on: boolean) => {
                                  if (!on) {
                                      pressedOff.add(frame.agent_id);
                                      capture.setHearers(hearingAgents());
                                  }
                                  core.send({ type: PARTICIPATION_SIGNAL.AGENT_HEARING, agent_id: frame.agent_id, on });
                              },
                          },
                seeing:
                    seeing === undefined
                        ? undefined
                        : {
                              on: seeing,
                              onChange: (on: boolean) => {
                                  core.send({ type: PARTICIPATION_SIGNAL.AGENT_SEEING, agent_id: frame.agent_id, on });
                              },
                          },
                looking,
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
        const agent: ShownAgent = { ...shown.get(frame.agent_id), joined: frame };
        shown.set(frame.agent_id, agent);
        draw(agent);
    }

    function showHearing(frame: AgentHearingFrame) {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        pressedOff.delete(frame.agent_id);
        const recorded = { ...agent, hearing: frame.on };
        shown.set(frame.agent_id, recorded);
        draw(recorded);
        capture.setHearers(hearingAgents());
    }

    function showSeeing(frame: AgentSeeingFrame) {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        const recorded = { ...agent, seeing: frame.on };
        shown.set(frame.agent_id, recorded);
        draw(recorded);
    }

    // The mark is drawn at each look and leaves after a while; a look in between starts the time over.
    const markTimers = new Map<string, ReturnType<typeof setTimeout>>();

    function showLooked(frame: AgentLookedFrame) {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        clearTimeout(markTimers.get(frame.agent_id));
        setLooking(agent, true);
        markTimers.set(
            frame.agent_id,
            setTimeout(() => {
                markTimers.delete(frame.agent_id);
                // An agent that left clears the timer, so the agent is still shown.
                setLooking(shown.get(frame.agent_id) as ShownAgent, false);
            }, LOOK_MARK_MILLISECONDS),
        );
    }

    function setLooking(agent: ShownAgent, looking: boolean) {
        const marked = { ...agent, looking };
        shown.set(agent.joined.agent_id, marked);
        draw(marked);
    }

    const offers = document.createElement("div");
    offers.id = "agentOffers";
    offers.tabIndex = -1;
    const offersView = createAgentOffers();
    // The last offers frame and how each agent's last invitation ended, which the row shows until a new call.
    let offered: Offer[] = [];
    const endings = new Map<string, AgentControlOutcome>();
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
        offersView.full = false;
        offersView.rows = [];
        offered = [];
        endings.clear();
        clearRefusal();
    }

    function drawOffers() {
        offersView.rows = rowsOf(offered, endings);
    }

    // The kit replaces the button that had focus when its row turns inviting, and focus would fall to the page.
    function showOffers(frame: AgentOffersFrame) {
        const hadFocus = offers.contains(document.activeElement);
        clearRefusal();
        offered = frame.offers;
        const pending = new Set(offered.filter((offer) => offer.pending).map((offer) => offer.agent_key));
        const listed = new Set(offered.map((offer) => offer.agent_key));
        for (const key of endings.keys()) if (pending.has(key) || !listed.has(key)) endings.delete(key);
        offersView.shown = frame.shown;
        offersView.full = frame.full;
        drawOffers();
        flushSync();
        if (hadFocus && !offers.contains(document.activeElement)) offers.focus();
    }

    function showEnding(frame: InvitationEndedFrame) {
        endings.set(frame.agent_key, outcomeOf(frame.outcome));
        drawOffers();
    }

    function hide(agentId: string) {
        shown.delete(agentId);
        pressedOff.delete(agentId);
        speaking.delete(agentId);
        clearTimeout(markTimers.get(agentId));
        markTimers.delete(agentId);
        voices.close(agentId);
        core.showAgentTile(null, tileKey(agentId));
        capture.setHearers(hearingAgents());
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
    core.on(PARTICIPATION_SIGNAL.AGENT_SEEING, showSeeing);
    core.on(PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST, (frame: AgentLookRequestFrame) => {
        void looks.answer(frame.request_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_LOOKED, showLooked);
    core.on(PARTICIPATION_SIGNAL.AGENT_VOICE, (frame: AgentVoiceFrame) => {
        if (shown.has(frame.agent_id)) voices.play(frame.agent_id, frame.voice_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_STOP, (frame: AgentStopFrame) => {
        voices.stop(frame.agent_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_OFFERS, showOffers);
    core.on(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, showEnding);
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
