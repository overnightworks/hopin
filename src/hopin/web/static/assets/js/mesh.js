import { monitorConnectionQuality } from "./quality.js";
import { SIGNAL_TYPE, on, send } from "./signaling.js";
import { addRemoteTile, removeRemoteTile, showRemoteStream } from "./stage.js";
import { state } from "./state.js";

const RTC_CONFIG = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };

const connectionsByParticipant = new Map();
let peersChanged = () => {};

export function onPeersChanged(callback) {
    peersChanged = callback;
}

export function peerCount() {
    return connectionsByParticipant.size;
}

async function createPeerConnection(participantId, initiator) {
    const connection = new RTCPeerConnection(RTC_CONFIG);
    connectionsByParticipant.set(participantId, connection);

    if (state.localStream) {
        for (const track of state.localStream.getTracks()) {
            connection.addTrack(track, state.localStream);
        }
    }

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
        const offer = await connection.createOffer();
        await connection.setLocalDescription(offer);
        send({
            type: SIGNAL_TYPE.OFFER,
            target: participantId,
            sdp: connection.localDescription,
        });
    }

    monitorConnectionQuality(participantId, connection);
    return connection;
}

async function handleOffer(data) {
    const connection =
        connectionsByParticipant.get(data.from) ?? (await createPeerConnection(data.from, false));
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp));
    const answer = await connection.createAnswer();
    await connection.setLocalDescription(answer);
    send({
        type: SIGNAL_TYPE.ANSWER,
        target: data.from,
        sdp: connection.localDescription,
    });
}

async function handleAnswer(data) {
    await connectionsByParticipant.get(data.from)?.setRemoteDescription(new RTCSessionDescription(data.sdp));
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
