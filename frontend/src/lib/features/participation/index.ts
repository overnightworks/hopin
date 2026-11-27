import { loadKitWords } from "overnightworks-design";
import { createPlayer as createSpeechPlayer, startCapture } from "overnightworks-speech-browser";
import { flushSync, mount } from "svelte";
import { AGENT_ACTIVITY, type FeatureCore } from "#lib/call/feature-port.ts";
import { featureView } from "#lib/ui/chat.svelte.ts";
import { language, words } from "#lib/words.ts";
import { SIGNAL_TYPE, type ServerFrameByType } from "#lib/wire.ts";
import "./participation.css";
import AgentLine from "./AgentLine.svelte";
import AgentOffers from "./AgentOffers.svelte";
import { createHearingCapture } from "./hearing.ts";
import { createLooks } from "./looking.ts";
import {
    createAgentOffers,
    dropLapsedEnding,
    hereRowOf,
    keepStandingEndings,
    outcomeOf,
    refusalOf,
    rowsOf,
    type Ending,
} from "./offers.svelte.ts";
import { createAgentVoices, type HeldPlace, type PlayerFactory } from "./voice.ts";
import {
    AGENT_REFUSAL,
    HEARING_PATH,
    PARTICIPATION_SIGNAL,
    type Offer,
    type ParticipationFrameByType,
    type FIGURE_FRAMING,
    type TURN_OUTCOME,
} from "./wire.ts";

/** Tells the server where the player stood in the sentence a hold stopped, when a sound of the agent had started here. */
function reportHold(core: FeatureCore, agentId: string, place: HeldPlace | null) {
    if (place === null) return;
    core.send({
        type: PARTICIPATION_SIGNAL.AGENT_HOLD_PLAYED,
        agent_id: agentId,
        voice_id: place.voiceId,
        played_seconds: place.playedSeconds,
    });
}

interface ShownAgent {
    readonly joined: ParticipationFrameByType[PARTICIPATION_SIGNAL.AGENT_JOINED];
    // The server's record of the sponsor's switches; absent until its frame comes, which only the sponsor gets.
    readonly hearing?: boolean;
    readonly seeing?: boolean;
    // A look of the agent was shown lately.
    readonly looking?: boolean;
    // Why the agent's turns do not answer, as it stated it last; absent when it states none.
    readonly turnOutcome?: TURN_OUTCOME;
    // The framing the agent took after it joined; absent until its frame comes, the joined frame carries the one before.
    readonly framing?: FIGURE_FRAMING;
}

// How long the mark of a look stays on the agent's tile.
const LOOK_MARK_MILLISECONDS = 3000;

const NOT_REMOVED_TEXT = words["agent.notRemovedToast"];

function tileKey(agentId: string) {
    return `agent-${agentId}`;
}

export function install(core: FeatureCore<ParticipationFrameByType>): Promise<void> {
    return installWith(core, createSpeechPlayer);
}

/** `createPlayer` makes the player of each agent that speaks. */
export async function installWith(
    core: FeatureCore<ParticipationFrameByType>,
    createPlayer: PlayerFactory,
): Promise<void> {
    const kitWords = await loadKitWords(language);
    let seatId = "";
    let isHost = false;
    const shown = new Map<string, ShownAgent>();
    const capture = createHearingCapture(
        () => `${location.protocol.replace("http", "ws")}//${location.host}${HEARING_PATH}/${seatId}`,
        startCapture,
    );
    const looks = createLooks(
        (bounds) => core.takePicture(bounds),
        (frame, options) => {
            core.send(frame, options);
        },
    );
    // Agents whose sound plays now: their tile shows them speaking.
    const speaking = new Set<string>();
    const voices = createAgentVoices(createPlayer, (agentId, sentence) => {
        core.speakAgentFigure(tileKey(agentId), sentence);
        const wasSpeaking = speaking.has(agentId);
        if (sentence === null) speaking.delete(agentId);
        else speaking.add(agentId);
        // Only a player that is still open reports, and `hide` closes the player of an agent before it leaves `shown`.
        if (speaking.has(agentId) !== wasSpeaking) draw(shown.get(agentId) as ShownAgent);
    });
    // Agents that stated that a turn of theirs runs: their tile is named as thinking while none of their sound plays.
    const thinking = new Set<string>();
    // Agents whose switch the person pressed off and whose record has not answered yet: until it does, the capture
    // stays closed, for no sound sent after the press may reach them, whichever other agents still hear the person.
    const pressedOff = new Set<string>();

    function hearingAgents() {
        if (pressedOff.size > 0) return 0;
        return [...shown.values()].filter((agent) => agent.hearing === true).length;
    }

    function mayRemove(frame: ParticipationFrameByType[PARTICIPATION_SIGNAL.AGENT_JOINED]) {
        return isHost || frame.sponsor_id === seatId;
    }

    function activityOf(agentId: string) {
        if (speaking.has(agentId)) return AGENT_ACTIVITY.SPEAKING;
        return thinking.has(agentId) ? AGENT_ACTIVITY.THINKING : AGENT_ACTIVITY.PRESENT;
    }

    function draw({ joined: frame, hearing, seeing, looking, framing }: ShownAgent) {
        core.showAgentTile(
            {
                name: frame.name,
                activity: activityOf(frame.agent_id),
                sponsor: frame.sponsor,
                figure: frame.figure,
                gestures: frame.gestures,
                room: frame.room,
                framing: framing ?? frame.framing,
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
                listening: {
                    on: !voices.isMuted(frame.agent_id),
                    onChange: (on: boolean) => {
                        voices.setMuted(frame.agent_id, !on);
                        draw(shown.get(frame.agent_id) as ShownAgent);
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

    // The chat sends its lines to the agents shown now, and only then; the server holds them for the agents still joined.
    function readLines() {
        core.setLineReaders({
            names: [...shown.values()].map((agent) => agent.joined.name),
            frameType: PARTICIPATION_SIGNAL.LINE_FOR_AGENTS,
            send: (text) => {
                core.send({ type: PARTICIPATION_SIGNAL.LINE_FOR_AGENTS, text });
            },
        });
    }

    function show(frame: ParticipationFrameByType[PARTICIPATION_SIGNAL.AGENT_JOINED]) {
        const agent: ShownAgent = { ...shown.get(frame.agent_id), joined: frame };
        const isNew = !shown.has(frame.agent_id);
        shown.set(frame.agent_id, agent);
        draw(agent);
        drawOffers();
        if (isNew) readLines();
    }

    function showHearing(frame: ParticipationFrameByType[PARTICIPATION_SIGNAL.AGENT_HEARING]) {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        pressedOff.delete(frame.agent_id);
        const recorded = { ...agent, hearing: frame.on };
        shown.set(frame.agent_id, recorded);
        draw(recorded);
        capture.setHearers(hearingAgents());
    }

    function showSeeing(frame: ParticipationFrameByType[PARTICIPATION_SIGNAL.AGENT_SEEING]) {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        const recorded = { ...agent, seeing: frame.on };
        shown.set(frame.agent_id, recorded);
        draw(recorded);
    }

    // The mark is drawn at each look and leaves after a while; a look in between starts the time over.
    const markTimers = new Map<string, ReturnType<typeof setTimeout>>();

    function showLooked(frame: ParticipationFrameByType[PARTICIPATION_SIGNAL.AGENT_LOOKED]) {
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
    const endings = new Map<string, Ending>();
    mount(AgentOffers, {
        target: offers,
        props: {
            view: offersView,
            words: kitWords,
            onOutcomeEnd: (agentKey: string) => {
                dropLapsedEnding(endings, offered, agentKey);
                drawOffers();
            },
            onCall: (agentKey: string) => {
                clearRefusal();
                core.send({ type: PARTICIPATION_SIGNAL.AGENT_ADD, agent_key: agentKey });
            },
        },
    });
    core.addMoreItems(offers);
    // A refusal leaves with the next offers frame (the server sends an add's own offers frame before its refusal) and
    // when the person closes More, for the server sends no frame when the person only lost the right. Every ended row
    // leaves with the control too.
    offers.closest("dialog")?.addEventListener("close", () => {
        clearRefusal();
        endings.clear();
        drawOffers();
    });

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
        offersView.rows = rowsOf(
            [...shown.values()].map(({ joined, turnOutcome }) => hereRowOf(joined.agent_id, joined.name, turnOutcome)),
            offered,
            endings,
        );
    }

    // The kit replaces the button that had focus when its row turns inviting, and focus would fall to the page.
    function showOffers(frame: ParticipationFrameByType[PARTICIPATION_SIGNAL.AGENT_OFFERS]) {
        const hadFocus = offers.contains(document.activeElement);
        clearRefusal();
        offered = frame.offers;
        keepStandingEndings(endings, offered);
        offersView.shown = frame.shown;
        offersView.full = frame.full;
        drawOffers();
        flushSync();
        if (hadFocus && !offers.contains(document.activeElement)) offers.focus();
    }

    function showEnding(frame: ParticipationFrameByType[PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED]) {
        endings.set(frame.agent_key, { name: frame.name, outcome: outcomeOf(frame.outcome) });
        drawOffers();
    }

    function hide(agentId: string) {
        shown.delete(agentId);
        pressedOff.delete(agentId);
        speaking.delete(agentId);
        thinking.delete(agentId);
        clearTimeout(markTimers.get(agentId));
        markTimers.delete(agentId);
        voices.close(agentId);
        core.showAgentTile(null, tileKey(agentId));
        drawOffers();
        capture.setHearers(hearingAgents());
        readLines();
    }

    const seated = (
        frame: ServerFrameByType[SIGNAL_TYPE.ROOM_CREATED] | ServerFrameByType[SIGNAL_TYPE.EXISTING_PARTICIPANTS],
    ) => {
        seatId = frame.participant_id;
        isHost = frame.is_host;
        // The server announces every agent still in the room to a new seat; one it does not announce has left.
        for (const agentId of shown.keys()) hide(agentId);
        clearOffers();
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, seated);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, seated);
    core.on(PARTICIPATION_SIGNAL.AGENT_JOINED, show);
    core.on(PARTICIPATION_SIGNAL.AGENT_LEFT, (frame) => {
        hide(frame.agent_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_HEARING, showHearing);
    core.on(PARTICIPATION_SIGNAL.AGENT_SEEING, showSeeing);
    core.on(PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST, (frame) => {
        void looks.answer(frame.request_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_LOOKED, showLooked);
    core.on(PARTICIPATION_SIGNAL.AGENT_VOICE, (frame) => {
        if (shown.has(frame.agent_id)) voices.play(frame.agent_id, frame.voice_id, frame.mood);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_HELD, (frame) => {
        if (shown.has(frame.agent_id)) reportHold(core, frame.agent_id, voices.pause(frame.agent_id));
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_CONTINUED, (frame) => {
        voices.resume(frame.agent_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_STOP, (frame) => {
        voices.stop(frame.agent_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_GESTURE, (frame) => {
        if (shown.has(frame.agent_id)) core.gestureAgentFigure(tileKey(frame.agent_id), frame.gesture);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_FRAMING, (frame) => {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        const recorded = { ...agent, framing: frame.framing };
        shown.set(frame.agent_id, recorded);
        draw(recorded);
        core.frameAgentFigure(tileKey(frame.agent_id), frame.framing);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_THINKING, (frame) => {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        core.thinkAgentFigure(tileKey(frame.agent_id), frame.on);
        if (frame.on) thinking.add(frame.agent_id);
        else thinking.delete(frame.agent_id);
        draw(agent);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, (frame) => {
        const agent = shown.get(frame.agent_id);
        if (agent === undefined) return;
        shown.set(frame.agent_id, { ...agent, turnOutcome: frame.outcome ?? undefined });
        drawOffers();
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_OFFERS, showOffers);
    core.on(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, showEnding);
    core.on(PARTICIPATION_SIGNAL.AGENT_LINE, (frame) => {
        core.receiveChatLine(featureView(AgentLine, { name: frame.name, text: frame.text }));
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_REFUSED, (frame) => {
        if (frame.reason === AGENT_REFUSAL.NOT_SPONSOR_OR_HOST) {
            core.showToast(NOT_REMOVED_TEXT, core.toastKind.LOCKED, null);
        } else {
            offersView.refusal = refusalOf(frame.reason);
        }
    });
    core.onAgentFigureEnded((key, code) => {
        const agentId = [...shown.keys()].find((id) => tileKey(id) === key);
        if (agentId === undefined) return;
        core.send({ type: PARTICIPATION_SIGNAL.AGENT_FIGURE_ENDED, agent_id: agentId, code });
    });
    core.onMicrophone((stream) => {
        capture.setStream(stream);
    });
    core.onCallEnded(() => {
        looks.end();
        for (const agentId of shown.keys()) hide(agentId);
        clearOffers();
    });
}
