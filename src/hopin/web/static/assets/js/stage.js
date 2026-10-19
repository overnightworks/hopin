import { createQualityIndicator } from "./quality.js";
import { state } from "./state.js";

const localVideo = document.getElementById("localVideo");
const localPlaceholder = document.getElementById("localPlaceholder");
const localContainer = document.getElementById("localContainer");
const stage = document.getElementById("stage");
const tiles = document.querySelector("#videoGrid .tiles");

const localMuteMark = localContainer.querySelector(".muted");
const remoteMediaStates = new Map();
const remotesWithoutPicture = new Set();

function renderRemoteVideo(participantId) {
    const video = document.getElementById(`video-${participantId}`);
    const placeholder = document.getElementById(`placeholder-${participantId}`);
    if (!video) return;
    const lacksVideoTrack = video.srcObject !== null && video.srcObject.getVideoTracks().length === 0;
    const announcedOff = remoteMediaStates.get(participantId)?.video === false;
    const showsVideo = !lacksVideoTrack && !announcedOff && !remotesWithoutPicture.has(participantId);
    video.classList.toggle("hidden", !showsVideo);
    placeholder.classList.toggle("hidden", showsVideo);
}

function renderRemoteMuteMark(participantId) {
    const muteMark = document.querySelector(`#container-${participantId} .muted`);
    if (!muteMark) return;
    muteMark.classList.toggle("hidden", remoteMediaStates.get(participantId)?.audio !== false);
}

function renderRemoteMediaState(participantId) {
    renderRemoteVideo(participantId);
    renderRemoteMuteMark(participantId);
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

export function showLocalStream(stream) {
    localVideo.srcObject = stream;
    localContainer.classList.toggle("mirror", !state.screenStream);
}

export function updateLocalPreview() {
    const showsVideo = state.hasCamera && state.videoEnabled;
    localVideo.classList.toggle("hidden", !showsVideo);
    localPlaceholder.classList.toggle("hidden", showsVideo);
    localMuteMark.classList.toggle("hidden", state.audioEnabled);
}

export function setRemoteMediaState(participantId, mediaState) {
    remoteMediaStates.set(participantId, mediaState);
    renderRemoteMediaState(participantId);
}

export function addRemoteTile(participantId) {
    const container = document.createElement("div");
    container.className = "tile";
    container.id = `container-${participantId}`;

    const video = document.createElement("video");
    video.id = `video-${participantId}`;
    video.autoplay = true;
    video.playsinline = true;

    const placeholder = document.createElement("div");
    placeholder.className = "vid off hidden";
    placeholder.id = `placeholder-${participantId}`;
    placeholder.appendChild(createPersonAvatar());

    const label = document.createElement("span");
    label.className = "name";
    label.append(localMuteMark.cloneNode(true), `Peer ${participantId.slice(0, 6)}`);

    container.append(video, placeholder, label, createQualityIndicator());
    tiles.appendChild(container);
    updateGridLayout();
    renderRemoteMediaState(participantId);

    video.addEventListener("loadedmetadata", () => {
        if (video.videoWidth === 0) remotesWithoutPicture.add(participantId);
        renderRemoteVideo(participantId);
    });
}

export function removeRemoteTile(participantId) {
    remoteMediaStates.delete(participantId);
    remotesWithoutPicture.delete(participantId);
    const container = document.getElementById(`container-${participantId}`);
    if (!container) return false;
    container.remove();
    updateGridLayout();
    return true;
}

export function showRemoteStream(participantId, stream) {
    const video = document.getElementById(`video-${participantId}`);
    if (!video) return;
    video.srcObject = stream;
    renderRemoteVideo(participantId);
}
