import { createQualityIndicator } from "./quality.js";
import { state } from "./state.js";

const localVideo = document.getElementById("localVideo");
const localPlaceholder = document.getElementById("localPlaceholder");
const videoGrid = document.getElementById("videoGrid");

function showPlaceholder(video, placeholder) {
    video.classList.add("hidden");
    placeholder.classList.remove("hidden");
}

function updateGridLayout() {
    const tileCount = videoGrid.children.length;
    videoGrid.className = "video-grid";
    if (tileCount === 2) {
        videoGrid.classList.add("two");
    } else if (tileCount > 2) {
        videoGrid.classList.add("many");
    }
}

export function showLocalStream(stream) {
    localVideo.srcObject = stream;
}

export function updateLocalPreview() {
    const showsVideo = state.hasCamera && state.videoEnabled;
    localVideo.classList.toggle("hidden", !showsVideo);
    localPlaceholder.classList.toggle("hidden", showsVideo);
}

export function addRemoteTile(participantId) {
    const container = document.createElement("div");
    container.className = "video-container";
    container.id = `container-${participantId}`;

    const video = document.createElement("video");
    video.id = `video-${participantId}`;
    video.autoplay = true;
    video.playsinline = true;

    const placeholder = document.createElement("div");
    placeholder.className = "video-placeholder hidden";
    placeholder.id = `placeholder-${participantId}`;
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = participantId.slice(0, 2).toUpperCase();
    placeholder.appendChild(avatar);

    const label = document.createElement("div");
    label.className = "video-label";
    label.textContent = `Peer ${participantId.slice(0, 6)}`;

    container.append(video, placeholder, label, createQualityIndicator());
    videoGrid.appendChild(container);
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
