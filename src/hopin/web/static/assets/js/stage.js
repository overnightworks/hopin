import { createQualityIndicator } from "./quality.js";
import { state } from "./state.js";

const localVideo = document.getElementById("localVideo");
const localPlaceholder = document.getElementById("localPlaceholder");
const localContainer = document.getElementById("localContainer");
const prejoinVideo = document.getElementById("prejoinVideo");
const prejoinPlaceholder = document.getElementById("prejoinPlaceholder");
const ownPictures = [
    { video: localVideo, placeholder: localPlaceholder },
    { video: prejoinVideo, placeholder: prejoinPlaceholder },
];
const stage = document.getElementById("stage");
const tiles = document.querySelector("#videoGrid .tiles");

const localMuteMark = localContainer.querySelector(".muted");
const remoteMediaStates = new Map();
const WAVE_BAR_COUNT = 3;
const speakingParticipantIds = new Set();

function renderRemoteVideo(participantId) {
    const video = document.getElementById(`video-${participantId}`);
    const placeholder = document.getElementById(`placeholder-${participantId}`);
    if (!video) return;
    const pictureArrives = (video.srcObject?.getVideoTracks() ?? []).some(
        (track) => track.readyState === "live" && !track.muted,
    );
    const canDrawAFrame = video.readyState >= video.HAVE_CURRENT_DATA;
    const announcedOff = remoteMediaStates.get(participantId)?.video === false;
    const showsVideo = pictureArrives && canDrawAFrame && !announcedOff;
    video.classList.toggle("hidden", !showsVideo);
    placeholder.classList.toggle("hidden", showsVideo);
}

function renderRemoteVoice(participantId) {
    const container = document.getElementById(`container-${participantId}`);
    if (!container) return;
    const muted = remoteMediaStates.get(participantId)?.audio === false;
    const showsWave = speakingParticipantIds.has(participantId) && !muted;
    container.querySelector(".muted").classList.toggle("hidden", !muted);
    container.querySelector(".wave").classList.toggle("hidden", !showsWave);
    container.classList.toggle("speaking", showsWave);
}

function renderRemoteMediaState(participantId) {
    renderRemoteVideo(participantId);
    renderRemoteVoice(participantId);
}

function updateGridLayout() {
    stage.dataset.count = tiles.children.length;
}

function createPersonAvatar() {
    const avatar = document.createElement("span");
    avatar.className = "av";
    avatar.append(localPlaceholder.querySelector("svg").cloneNode(true));
    return avatar;
}

function createWave() {
    const wave = document.createElement("span");
    wave.className = "wave hidden";
    wave.setAttribute("aria-hidden", "true");
    wave.append(...Array.from({ length: WAVE_BAR_COUNT }, () => document.createElement("i")));
    return wave;
}

export function showLocalStream(stream) {
    for (const { video } of ownPictures) {
        video.srcObject = stream;
    }
    localContainer.classList.toggle("mirror", !state.screenStream);
}

export function updateLocalPreview() {
    const showsVideo = state.hasCamera && state.videoEnabled;
    for (const { video, placeholder } of ownPictures) {
        video.classList.toggle("hidden", !showsVideo);
        placeholder.classList.toggle("hidden", showsVideo);
    }
    localMuteMark.classList.toggle("hidden", state.audioEnabled);
}

export function setRemoteMediaState(participantId, mediaState) {
    remoteMediaStates.set(participantId, mediaState);
    renderRemoteMediaState(participantId);
}

export function setRemoteSpeaking(participantId, speaking) {
    if (speaking) speakingParticipantIds.add(participantId);
    else speakingParticipantIds.delete(participantId);
    renderRemoteVoice(participantId);
}

export function addRemoteTile(participantId) {
    const container = document.createElement("div");
    container.className = "tile";
    container.id = `container-${participantId}`;

    const video = document.createElement("video");
    video.id = `video-${participantId}`;
    video.autoplay = true;
    video.playsinline = true;
    video.addEventListener("loadeddata", () => renderRemoteVideo(participantId));

    const placeholder = document.createElement("div");
    placeholder.className = "vid off hidden";
    placeholder.id = `placeholder-${participantId}`;
    placeholder.appendChild(createPersonAvatar());

    const label = document.createElement("span");
    label.className = "name";
    label.append(localMuteMark.cloneNode(true), createWave(), `Peer ${participantId.slice(0, 6)}`);

    container.append(video, placeholder, label, createQualityIndicator());
    tiles.appendChild(container);
    updateGridLayout();
    renderRemoteMediaState(participantId);
}

export function removeRemoteTile(participantId) {
    remoteMediaStates.delete(participantId);
    speakingParticipantIds.delete(participantId);
    const container = document.getElementById(`container-${participantId}`);
    if (!container) return false;
    container.remove();
    updateGridLayout();
    return true;
}

const tracksWatched = new WeakSet();

// A media element does not start playing while the stream holds a video track that
// has received no frame yet, which silences the person's voice until the camera is on.
// So the element gets the audio, and each video track joins when its first frame arrives.
function attachTracksThatHaveData(participantId, stream) {
    const video = document.getElementById(`video-${participantId}`);
    if (!video) return;
    const attached = video.srcObject?.getTracks() ?? [];
    const wanted = stream.getTracks().filter((track) => track.kind === "audio" || !track.muted);
    const unchanged = wanted.length === attached.length && wanted.every((track) => attached.includes(track));
    if (!unchanged) video.srcObject = new MediaStream(wanted);
    renderRemoteVideo(participantId);
}

function watchVideoTrack(participantId, stream, track) {
    if (tracksWatched.has(track)) return;
    tracksWatched.add(track);
    track.addEventListener("unmute", () => attachTracksThatHaveData(participantId, stream));
    track.addEventListener("mute", () => renderRemoteVideo(participantId));
}

export function showRemoteStream(participantId, stream) {
    attachTracksThatHaveData(participantId, stream);
    for (const track of stream.getVideoTracks()) {
        watchVideoTrack(participantId, stream, track);
    }
}
