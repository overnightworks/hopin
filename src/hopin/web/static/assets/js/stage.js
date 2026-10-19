import { createQualityIndicator } from "./quality.js";
import { state } from "./state.js";

const localVideo = document.getElementById("localVideo");
const localPlaceholder = document.getElementById("localPlaceholder");
const localContainer = document.getElementById("localContainer");
const stage = document.getElementById("stage");
const tiles = document.querySelector("#videoGrid .tiles");

function showPlaceholder(video, placeholder) {
    video.classList.add("hidden");
    placeholder.classList.remove("hidden");
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
    label.textContent = `Peer ${participantId.slice(0, 6)}`;

    container.append(video, placeholder, label, createQualityIndicator());
    tiles.appendChild(container);
    updateGridLayout();

    video.addEventListener("loadedmetadata", () => {
        if (video.videoWidth === 0) showPlaceholder(video, placeholder);
    });
}

export function removeRemoteTile(participantId) {
    const container = document.getElementById(`container-${participantId}`);
    if (!container) return false;
    container.remove();
    updateGridLayout();
    return true;
}

export function showRemoteStream(participantId, stream) {
    const video = document.getElementById(`video-${participantId}`);
    const placeholder = document.getElementById(`placeholder-${participantId}`);
    if (!video) return;
    video.srcObject = stream;
    if (stream.getVideoTracks().length === 0 && placeholder) {
        showPlaceholder(video, placeholder);
    }
}
