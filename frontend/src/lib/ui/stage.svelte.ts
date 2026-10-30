import { SvelteMap, SvelteSet } from "svelte/reactivity";
import { untrack } from "svelte";
import type { ConnectionQuality, RemoteMediaState } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";

// The starting values are what the layout's markup shows before the first call.
export const ownPicture = $state<{
    stream: MediaStream | null;
    delivery: number;
    mirrored: boolean;
    showsVideo: boolean;
    muted: boolean;
}>({ stream: null, delivery: 0, mirrored: true, showsVideo: true, muted: false });

// The call state is read when these run, not reactively: the mirror follows a screen share only at the next stream.
export function showLocalStream(stream: MediaStream | null) {
    ownPicture.stream = stream;
    // The same stream arrives again after a track swap; the counter is what makes the video take it again.
    ownPicture.delivery += 1;
    ownPicture.mirrored = !state.screenStream;
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

// A media state can arrive before its tile and a speaking report after the tile is gone, so these follow the person, not the tile.
const mediaStates = new SvelteMap<string, RemoteMediaState>();
const speakingParticipantIds = new SvelteSet<string>();

// Streams, qualities and elements stay out of the tiles' reactive state: the media actions read them without becoming dependent.
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- deliberately not reactive
const remoteStreams = new Map<string, MediaStream>();
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
            return speakingParticipantIds.has(this.id) && !this.isMuted;
        },
    };
}

export function remoteStreamOf(participantId: string) {
    return remoteStreams.get(participantId);
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
    speakingParticipantIds.delete(participantId);
    remoteStreams.delete(participantId);
    remoteQualities.delete(participantId);
    const index = remoteTiles.findIndex((tile) => tile.id === participantId);
    if (index === -1) return false;
    remoteTiles.splice(index, 1);
    return true;
}

export function showRemoteStream(participantId: string, stream: MediaStream) {
    const tile = findTile(participantId);
    if (!tile) return;
    remoteStreams.set(participantId, stream);
    tile.streamDelivery += 1;
}

export function showRemoteName(participantId: string) {
    const tile = findTile(participantId);
    if (tile) tile.name = state.namesByParticipant.get(participantId) ?? "";
}

export function setRemoteMediaState(participantId: string, mediaState: RemoteMediaState) {
    mediaStates.set(participantId, mediaState);
    refreshPictureReady(participantId);
}

export function setRemoteSpeaking(participantId: string, speaking: boolean) {
    if (speaking) speakingParticipantIds.add(participantId);
    else speakingParticipantIds.delete(participantId);
}

export function setConnectionQuality(participantId: string, quality: ConnectionQuality) {
    const tile = findTile(participantId);
    if (!tile) return;
    remoteQualities.set(participantId, quality);
    tile.qualityDelivery += 1;
}
