// The whole stage port: the own picture of the call tile and the pre-join preview and the remote tiles, which
// `Tiles.svelte` renders. The tiles' streams stay in plain maps outside the reactive state.

import { SvelteMap } from "svelte/reactivity";
import { untrack } from "svelte";
import {
    AGENT_ACTIVITY,
    AGENT_FIGURE,
    DEFAULT_AGENT_KEY,
    type AgentFigureDrawer,
    type AgentFigureHandle,
    type AgentGestureClips,
    type AgentSentence,
    type AgentTile,
} from "#lib/call/feature-port.ts";
import type { ConnectionQuality, RemoteMediaState } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";

// The starting values are what the layout's markup shows before the first call.
export const ownPicture = $state<{
    stream: MediaStream | null;
    delivery: number;
    showsVideo: boolean;
    muted: boolean;
}>({ stream: null, delivery: 0, showsVideo: true, muted: false });

export function showLocalStream(stream: MediaStream | null) {
    ownPicture.stream = stream;
    // The same stream arrives again after a track swap; the counter is what makes the video take it again.
    ownPicture.delivery += 1;
}

export function updateLocalPreview() {
    ownPicture.showsVideo = state.videoEnabled;
    ownPicture.muted = !state.audioEnabled;
}

export interface RemoteTile {
    readonly id: string;
    name: string;
    pictureReady: boolean;
    streamDelivery: number;
    qualityDelivery: number;
    readonly showsVideo: boolean;
    readonly isMuted: boolean;
    readonly isSpeaking: boolean;
}

// In join order: appended on add, spliced on remove, never sorted.
export const remoteTiles = $state<RemoteTile[]>([]);

export interface ScreenShare {
    readonly participantId: string;
    readonly name: string;
    streamDelivery: number;
}

// The screens being shared, each a tile of its own beside the sharer's camera tile.
export const screenShares = $state<ScreenShare[]>([]);

// The agents' tiles come from the features that own the agents; an agent is no person, so it has no stream and no place in the call. They stay in the order they were shown.
export const agentTiles = new SvelteMap<string, AgentTile>();

export function showAgentTile(tile: AgentTile | null, key: string = DEFAULT_AGENT_KEY) {
    if (tile === null) {
        agentTiles.delete(key);
        thinkingKeys.delete(key);
    } else agentTiles.set(key, tile);
    refreshListening();
}

/** The id of the person's own tile in the layout. */
export const OWN_TILE_ID = "localContainer";

/** The id of the tile of a person or an agent, by its participant id or its key. */
export function tileIdOf(key: string) {
    return "container-" + key;
}

/** Which of an agent tile's chips shrink to their icon: the see chip on a narrow tile and while the tile shows a figure, which leave less room; the hearing chip while the tile shows a figure and is not narrow, and on the narrow tile that holds all three switches (a sponsor's), where no place is left beside the others for its whole track; on any other narrow tile it stays whole and hopin.css moves it into a corner; the listen chip wherever its whole label and the see chip would not fit in one row: on a narrow or figured tile, on a tile too small for its label (`spacious` is false) and beside the see chip, which is whole only on a spacious tile with no figure. Only a sponsor's tile has a see chip, and it has the hearing chip too. */
export function compactChips(tile: { figured: boolean; narrow: boolean; spacious: boolean; seeing: boolean }) {
    return {
        hearing: tile.narrow ? tile.seeing : tile.figured,
        seeing: tile.narrow || tile.figured,
        listening: tile.narrow || tile.figured || tile.seeing || !tile.spacious,
    };
}

// The call core draws no figure itself: the feature that does hands over its drawer, and a tile without one or without a path stays as it is.
let figureDrawer: AgentFigureDrawer | null = null;

export function drawAgentFigures(drawer: AgentFigureDrawer) {
    figureDrawer = drawer;
}

let figureEndedHook: ((key: string, reason: string) => void) | null = null;

export function onAgentFigureEnded(hook: (key: string, reason: string) => void) {
    figureEndedHook = hook;
}

interface DrawnFigure {
    handle: AgentFigureHandle;
    // The id of the tile the figure was last turned toward, null for nobody.
    listensTo: string | null;
}

// The drawn figures by the key of their tile, which is how a gesture finds its agent; not reactive, as the media maps below.
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- deliberately not reactive
const drawnFigures = new Map<string, DrawnFigure>();

/** Play the gesture on the figure of the agent tile under `key`; a tile with no drawn figure drops it. */
export function gestureAgentFigure(key: string, gesture: string) {
    drawnFigures.get(key)?.handle.gesture(gesture);
}

/** Show the figure of the agent tile under `key` in the framing named `framing`; a tile with no drawn figure drops it. */
export function frameAgentFigure(key: string, framing: string) {
    drawnFigures.get(key)?.handle.frame(framing);
}

/** Make the figure of the agent tile under `key` speak the sentence, null stop; a tile with no figure drops it. */
export function speakAgentFigure(key: string, sentence: AgentSentence | null) {
    drawnFigures.get(key)?.handle.speak(sentence);
}

// The keys of the agent tiles whose agent thinks, kept apart from the drawn figures so a figure drawn later starts thinking.
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- deliberately not reactive
const thinkingKeys = new Set<string>();

/** Show whether the agent of the tile under `key` thinks, on its figure now and on one drawn there later. */
export function thinkAgentFigure(key: string, thinking: boolean) {
    if (thinking) thinkingKeys.add(key);
    else thinkingKeys.delete(key);
    drawnFigures.get(key)?.handle.think(thinking);
}

let ownSpeaking = false;

/** The ids of the tiles of everyone who speaks now, in the stage's order: the own tile, the people's, the agents'. */
function speakingTileIds(): string[] {
    return [
        ...(ownSpeaking ? [OWN_TILE_ID] : []),
        ...remoteTiles.filter((tile) => tile.isSpeaking).map((tile) => tileIdOf(tile.id)),
        ...[...agentTiles]
            .filter(([, agent]) => agent.activity === AGENT_ACTIVITY.SPEAKING)
            .map(([key]) => tileIdOf(key)),
    ];
}

/** Whom a figure listens to: the one it listens to while they still speak, else the first who speaks; never its own tile, and nobody in silence. */
function listeningTarget(current: string | null, speaking: readonly string[], ownTile: string): string | null {
    const others = speaking.filter((tileId) => tileId !== ownTile);
    if (current !== null && others.includes(current)) return current;
    return others[0] ?? null;
}

function refreshListening() {
    const speaking = speakingTileIds();
    for (const [key, figure] of drawnFigures) {
        const target = listeningTarget(figure.listensTo, speaking, tileIdOf(key));
        if (target === figure.listensTo) continue;
        figure.listensTo = target;
        figure.handle.listen(target === null ? null : document.getElementById(target));
    }
}

export function setOwnSpeaking(speaking: boolean) {
    ownSpeaking = speaking;
    refreshListening();
}

interface AgentFigureSpot {
    key: string;
    path: string | undefined;
    gestures?: AgentGestureClips;
    room?: string;
    framing?: string;
}

/** A Svelte action on an agent tile's video layer: `data-figure` holds how its figure stands, `data-figure-end` why it ended, and a new path draws afresh; the clips and the room go with the path they came with. */
export function agentFigure(host: HTMLElement, spot: AgentFigureSpot) {
    // The key is the tile's own and never changes while its action lives.
    const { key } = spot;
    let drawnPath = spot.path;
    let drawn: AgentFigureHandle | null = null;

    function stop() {
        drawn?.stop();
        drawn = null;
        drawnFigures.delete(key);
    }

    function draw(spotted: AgentFigureSpot) {
        const { path: figure, gestures, room, framing } = spotted;
        stop();
        delete host.dataset.figureEnd;
        if (figureDrawer === null || figure === undefined) {
            delete host.dataset.figure;
            return;
        }
        host.dataset.figure = AGENT_FIGURE.LOADING;
        drawn = figureDrawer(
            host,
            figure,
            (state, reason) => {
                host.dataset.figure = state;
                if (reason === undefined) return;
                host.dataset.figureEnd = reason;
                figureEndedHook?.(key, reason);
            },
            gestures,
            framing,
            room,
        );
        drawnFigures.set(key, { handle: drawn, listensTo: null });
        if (thinkingKeys.has(key)) drawn.think(true);
        refreshListening();
    }

    draw(spot);
    return {
        // Svelte calls update with the unchanged path whenever the tile's entry re-renders; only a new path draws afresh.
        update(next: AgentFigureSpot) {
            if (next.path === drawnPath) return;
            drawnPath = next.path;
            draw(next);
        },
        destroy: stop,
    };
}

// The one view a feature shows on the stage; while it is there the layout puts every tile into a strip.
export const stageSurface = $state<{ view: FeatureView | null }>({ view: null });

export function showStage(view: FeatureView | null) {
    stageSurface.view = view;
}

/** The tiles the stage lays out: the own one, the people's and the agents'. */
export function stageTileCount() {
    return remoteTiles.length + 1 + agentTiles.size;
}

// A media state can arrive before its tile and a speaking report after the tile is gone, so these follow the person, not the tile.
const mediaStates = new SvelteMap<string, RemoteMediaState>();
const speakingByParticipant = new SvelteMap<string, boolean>();

// Streams, qualities and elements stay out of the tiles' reactive state: the media actions read them without becoming dependent.
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- deliberately not reactive
const remoteStreams = new Map<string, MediaStream>();
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- deliberately not reactive
const screenStreams = new Map<string, MediaStream>();
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- deliberately not reactive
const remoteQualities = new Map<string, ConnectionQuality>();
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- deliberately not reactive
const remoteVideos = new Map<string, HTMLVideoElement>();

function findTile(participantId: string) {
    return remoteTiles.find((tile) => tile.id === participantId);
}

function createTile(participantId: string): RemoteTile {
    return {
        id: participantId,
        name: state.namesByParticipant.get(participantId) ?? "",
        pictureReady: false,
        streamDelivery: 0,
        qualityDelivery: 0,
        get showsVideo() {
            return this.pictureReady && mediaStates.get(this.id)?.video !== false;
        },
        get isMuted() {
            return mediaStates.get(this.id)?.audio === false;
        },
        get isSpeaking() {
            return speakingByParticipant.get(this.id) === true && !this.isMuted;
        },
    };
}

export function remoteStreamOf(participantId: string) {
    return remoteStreams.get(participantId);
}

export function screenStreamOf(participantId: string) {
    return screenStreams.get(participantId);
}

export function remoteQualityOf(participantId: string) {
    return remoteQualities.get(participantId);
}

export function remoteVideoOf(participantId: string) {
    return remoteVideos.get(participantId);
}

// Runs inside an action's update, which Svelte tracks; the refresh must not become one of its dependencies.
export function refreshPictureReady(participantId: string) {
    untrack(() => {
        const video = remoteVideos.get(participantId);
        const tile = findTile(participantId);
        if (!video || !tile) return;
        const pictureArrives = (video.srcObject as MediaStream | null)
            ?.getVideoTracks()
            .some((track) => track.readyState === "live" && !track.muted);
        tile.pictureReady = Boolean(pictureArrives) && video.readyState >= video.HAVE_CURRENT_DATA;
    });
}

export function registerRemoteVideo(participantId: string, video: HTMLVideoElement) {
    const onFirstFrame = () => {
        refreshPictureReady(participantId);
    };
    video.addEventListener("loadeddata", onFirstFrame);
    remoteVideos.set(participantId, video);
    return () => {
        video.removeEventListener("loadeddata", onFirstFrame);
        remoteVideos.delete(participantId);
    };
}

export function addRemoteTile(participantId: string) {
    remoteTiles.push(createTile(participantId));
}

export function removeRemoteTile(participantId: string) {
    mediaStates.delete(participantId);
    speakingByParticipant.delete(participantId);
    remoteStreams.delete(participantId);
    remoteQualities.delete(participantId);
    showRemoteScreen(participantId, null);
    const index = remoteTiles.findIndex((tile) => tile.id === participantId);
    if (index === -1) return false;
    remoteTiles.splice(index, 1);
    refreshListening();
    return true;
}

export function showRemoteStream(participantId: string, stream: MediaStream) {
    const tile = findTile(participantId);
    if (!tile) return;
    remoteStreams.set(participantId, stream);
    tile.streamDelivery += 1;
}

export function showRemoteScreen(participantId: string, stream: MediaStream | null) {
    const existing = screenShares.find((share) => share.participantId === participantId);
    if (stream === null) {
        screenStreams.delete(participantId);
        if (existing) screenShares.splice(screenShares.indexOf(existing), 1);
        return;
    }
    screenStreams.set(participantId, stream);
    if (existing) {
        existing.streamDelivery += 1;
    } else {
        screenShares.push({
            participantId,
            get name() {
                return findTile(participantId)?.name ?? "";
            },
            streamDelivery: 1,
        });
    }
}

export function showRemoteName(participantId: string) {
    const tile = findTile(participantId);
    if (tile) tile.name = state.namesByParticipant.get(participantId) ?? "";
}

export function setRemoteMediaState(participantId: string, mediaState: RemoteMediaState) {
    mediaStates.set(participantId, mediaState);
    refreshPictureReady(participantId);
    refreshListening();
}

export function setRemoteSpeaking(participantId: string, speaking: boolean) {
    speakingByParticipant.set(participantId, speaking);
    refreshListening();
}

export function setConnectionQuality(participantId: string, quality: ConnectionQuality) {
    const tile = findTile(participantId);
    if (!tile) return;
    remoteQualities.set(participantId, quality);
    tile.qualityDelivery += 1;
}
