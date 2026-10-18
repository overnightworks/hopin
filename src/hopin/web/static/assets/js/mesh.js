import { applyUploadCaps } from "./bandwidth.js";
import { monitorConnectionQuality } from "./quality.js";
import { watchIceConnection } from "./recovery.js";
import { SIGNAL_TYPE, on, send } from "./signaling.js";
import { addRemoteTile, removeRemoteTile, showRemoteStream } from "./stage.js";
import { state } from "./state.js";

const ICE_SERVERS_PATH = "/api/ice-servers";

let rtcConfiguration = { iceServers: [] };

export async function loadIceServers() {
    const response = await fetch(ICE_SERVERS_PATH);
    if (!response.ok) {
        throw new Error(`Could not load ICE servers (${response.status})`);
    }
    const { iceServers } = await response.json();
    rtcConfiguration = { iceServers };
}

const connectionsByParticipant = new Map();
const answerApplicationsByParticipant = new Map();
let peersChanged = () => {};

export function onPeersChanged(callback) {
    peersChanged = callback;
}

export function peerCount() {
    return connectionsByParticipant.size;
}

async function sendOffer(participantId, connection, options) {
    const offer = await connection.createOffer(options);
    await connection.setLocalDescription(offer);
    send({
        type: SIGNAL_TYPE.OFFER,
        target: participantId,
        sdp: connection.localDescription,
    });
}

function capUploads() {
    return applyUploadCaps([...connectionsByParticipant.values()]);
}

async function createPeerConnection(participantId, initiator) {
    const connection = new RTCPeerConnection(rtcConfiguration);
    connectionsByParticipant.set(participantId, connection);

    if (state.localStream) {
        for (const track of state.localStream.getTracks()) {
            connection.addTrack(track, state.localStream);
        }
    }

    void capUploads();
    addRemoteTile(participantId);
    peersChanged();

    connection.ontrack = (event) => {
        showRemoteStream(participantId, event.streams[0]);
    };

    connection.onicecandidate = (event) => {
        if (event.candidate) {
            send({
                type: SIGNAL_TYPE.ICE_CANDIDATE,
                target: participantId,
                candidate: event.candidate,
            });
        }
    };

    if (initiator) {
        await sendOffer(participantId, connection);
        watchIceConnection(connection, () => sendOffer(participantId, connection, { iceRestart: true }));
    }

    monitorConnectionQuality(participantId, connection);
    return connection;
}

async function handleOffer(data) {
    const connection =
        connectionsByParticipant.get(data.from) ?? (await createPeerConnection(data.from, false));
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp));
    const answer = await connection.createAnswer();
    if (connection.signalingState === "closed") return;
    await connection.setLocalDescription(answer);
    send({
        type: SIGNAL_TYPE.ANSWER,
        target: data.from,
        sdp: connection.localDescription,
    });
}

function handleAnswer(data) {
    const previous = answerApplicationsByParticipant.get(data.from) ?? Promise.resolve();
    const applied = previous.then(() => applyAnswer(data));
    answerApplicationsByParticipant.set(data.from, applied.catch(reportError));
}

async function applyAnswer(data) {
    const connection = connectionsByParticipant.get(data.from);
    if (connection?.signalingState !== "have-local-offer") return;
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp));
}

async function handleIceCandidate(data) {
    const connection = connectionsByParticipant.get(data.from);
    if (connection && data.candidate) {
        await connection.addIceCandidate(new RTCIceCandidate(data.candidate));
    }
}

function removePeer(participantId) {
    connectionsByParticipant.get(participantId)?.close();
    connectionsByParticipant.delete(participantId);
    answerApplicationsByParticipant.delete(participantId);
    void capUploads();
    if (removeRemoteTile(participantId)) peersChanged();
}

export function removeAllPeers() {
    for (const participantId of connectionsByParticipant.keys()) {
        removePeer(participantId);
    }
}

export function addTrackToPeers(track, stream) {
    for (const connection of connectionsByParticipant.values()) {
        connection.addTrack(track, stream);
    }
}

export function removeTrackFromPeers(track) {
    for (const connection of connectionsByParticipant.values()) {
        const sender = connection.getSenders().find((candidate) => candidate.track === track);
        if (sender) connection.removeTrack(sender);
    }
}

export function replaceVideoTrackOnPeers(newTrack) {
    for (const connection of connectionsByParticipant.values()) {
        const sender = connection.getSenders().find((candidate) => candidate.track?.kind === "video");
        sender?.replaceTrack(newTrack);
    }
}

export function registerMeshHandlers() {
    on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, (data) => {
        for (const participantId of data.participants) {
            void createPeerConnection(participantId, true);
        }
    });
    on(SIGNAL_TYPE.PARTICIPANT_LEFT, (data) => removePeer(data.participant_id));
    on(SIGNAL_TYPE.OFFER, handleOffer);
    on(SIGNAL_TYPE.ANSWER, handleAnswer);
    on(SIGNAL_TYPE.ICE_CANDIDATE, handleIceCandidate);
}
