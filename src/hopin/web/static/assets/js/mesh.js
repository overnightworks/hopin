import { applyUploadCaps, videoTransceivers } from "./bandwidth.js";
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
const remoteSignalingByParticipant = new Map();
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
        if (initiator && state.localStream.getVideoTracks().length === 0) {
            connection.addTransceiver("video", { direction: "sendrecv", streams: [state.localStream] });
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

function applyInArrivalOrder(participantId, step) {
    const queue = remoteSignalingByParticipant.get(participantId) ?? { tail: Promise.resolve() };
    remoteSignalingByParticipant.set(participantId, queue);
    queue.tail = queue.tail
        .then(() => {
            if (remoteSignalingByParticipant.get(participantId) === queue) return step();
        })
        .catch(reportError);
}

async function sendLocalVideoInAnswer(connection) {
    if (!state.localStream) return;
    const [videoTrack = null] = state.localStream.getVideoTracks();
    const answeringReceivers = videoTransceivers(connection).filter(
        (transceiver) => transceiver.direction === "recvonly" && typeof transceiver.sender.setStreams === "function",
    );
    for (const transceiver of answeringReceivers) {
        transceiver.direction = "sendrecv";
        transceiver.sender.setStreams(state.localStream);
        await transceiver.sender.replaceTrack(videoTrack);
    }
}

async function answerOffer(data) {
    const connection =
        connectionsByParticipant.get(data.from) ?? (await createPeerConnection(data.from, false));
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp));
    await sendLocalVideoInAnswer(connection);
    const answer = await connection.createAnswer();
    if (connection.signalingState === "closed") return;
    await connection.setLocalDescription(answer);
    send({
        type: SIGNAL_TYPE.ANSWER,
        target: data.from,
        sdp: connection.localDescription,
    });
    await capUploads();
}

async function applyAnswer(data) {
    const connection = connectionsByParticipant.get(data.from);
    if (connection?.signalingState !== "have-local-offer") return;
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp));
}

async function applyIceCandidate(data) {
    const connection = connectionsByParticipant.get(data.from);
    if (connection && data.candidate) {
        await connection.addIceCandidate(new RTCIceCandidate(data.candidate));
    }
}

function removePeer(participantId) {
    connectionsByParticipant.get(participantId)?.close();
    connectionsByParticipant.delete(participantId);
    remoteSignalingByParticipant.delete(participantId);
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

export async function replaceVideoTrackOnPeers(newTrack) {
    const transceivers = [...connectionsByParticipant.values()].flatMap(videoTransceivers);
    await Promise.all(transceivers.map((transceiver) => transceiver.sender.replaceTrack(newTrack)));
    await capUploads();
}

export function registerMeshHandlers() {
    on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, (data) => {
        for (const participantId of data.participants) {
            void createPeerConnection(participantId, true);
        }
    });
    on(SIGNAL_TYPE.PARTICIPANT_LEFT, (data) => removePeer(data.participant_id));
    on(SIGNAL_TYPE.OFFER, (data) => applyInArrivalOrder(data.from, () => answerOffer(data)));
    on(SIGNAL_TYPE.ANSWER, (data) => applyInArrivalOrder(data.from, () => applyAnswer(data)));
    on(SIGNAL_TYPE.ICE_CANDIDATE, (data) => applyInArrivalOrder(data.from, () => applyIceCandidate(data)));
}
