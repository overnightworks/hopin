import { applyUploadCaps, videoTransceivers } from "#lib/call/bandwidth.ts";
import { monitorConnectionQuality } from "#lib/call/quality.ts";
import { watchIceConnection } from "#lib/call/recovery.ts";
import { on, send, type ServerFrame } from "#lib/call/signaling.ts";
import { ICE_SERVERS_PATH, SIGNAL_TYPE } from "#lib/wire.ts";
import { type MonitoredConnection, monitorSpeaking } from "#lib/call/speaking.ts";
import { addRemoteTile, removeRemoteTile, showRemoteStream } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";

interface SignalFrame {
    from: string;
    sdp: RTCSessionDescriptionInit;
    candidate?: RTCIceCandidateInit;
}

let rtcConfiguration: RTCConfiguration = { iceServers: [] };

export class SignedOutError extends Error {}

export async function loadIceServers(): Promise<void> {
    const response = await fetch(ICE_SERVERS_PATH);
    if (response.status === 401) {
        throw new SignedOutError("Signed out");
    }
    if (!response.ok) {
        throw new Error(`Could not load ICE servers (${String(response.status)})`);
    }
    const { iceServers } = (await response.json()) as { iceServers: RTCIceServer[] };
    rtcConfiguration = { iceServers };
}

interface SignalingQueue {
    tail: Promise<unknown>;
}

const connectionsByParticipant = new Map<string, RTCPeerConnection>();
const remoteSignalingByParticipant = new Map<string, SignalingQueue>();
let peersChanged = () => {};

export function onPeersChanged(callback: () => void) {
    peersChanged = callback;
}

export function peerCount() {
    return connectionsByParticipant.size;
}

async function sendOffer(participantId: string, connection: RTCPeerConnection, options?: RTCOfferOptions) {
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

async function createPeerConnection(participantId: string, initiator: boolean) {
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
        const [stream] = event.streams;
        if (stream) showRemoteStream(participantId, stream);
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
    // The DOM types an audio level as optional; the monitor reads the audio receivers' sources only.
    monitorSpeaking(participantId, connection as MonitoredConnection);
    return connection;
}

function applyInArrivalOrder(participantId: string, step: () => Promise<void>) {
    const queue = remoteSignalingByParticipant.get(participantId) ?? { tail: Promise.resolve() };
    remoteSignalingByParticipant.set(participantId, queue);
    queue.tail = queue.tail
        .then(() => {
            return remoteSignalingByParticipant.get(participantId) === queue ? step() : undefined;
        })
        .catch(reportError);
}

async function sendLocalVideoInAnswer(connection: RTCPeerConnection) {
    if (!state.localStream) return;
    const [videoTrack = null] = state.localStream.getVideoTracks();
    const answeringReceivers = videoTransceivers(connection).filter(
        (transceiver) => transceiver.direction === "recvonly" && typeof transceiver.sender.setStreams === "function",
    );
    for (const transceiver of answeringReceivers) {
        transceiver.direction = "sendrecv";
        transceiver.sender.setStreams(state.localStream);
    }
    await Promise.all(answeringReceivers.map((transceiver) => transceiver.sender.replaceTrack(videoTrack)));
}

function isClosed(connection: RTCPeerConnection) {
    return connection.signalingState === "closed";
}

async function answerOffer(data: SignalFrame) {
    const connection = connectionsByParticipant.get(data.from) ?? (await createPeerConnection(data.from, false));
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp));
    await sendLocalVideoInAnswer(connection);
    if (isClosed(connection)) return;
    const answer = await connection.createAnswer();
    if (isClosed(connection)) return;
    await connection.setLocalDescription(answer);
    send({
        type: SIGNAL_TYPE.ANSWER,
        target: data.from,
        sdp: connection.localDescription,
    });
    await capUploads();
}

async function applyAnswer(data: SignalFrame) {
    const connection = connectionsByParticipant.get(data.from);
    if (connection?.signalingState !== "have-local-offer") return;
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp));
}

async function applyIceCandidate(data: SignalFrame) {
    const connection = connectionsByParticipant.get(data.from);
    if (connection && data.candidate) {
        await connection.addIceCandidate(new RTCIceCandidate(data.candidate));
    }
}

function removePeer(participantId: string) {
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

export function addTrackToPeers(track: MediaStreamTrack, stream: MediaStream) {
    for (const connection of connectionsByParticipant.values()) {
        connection.addTrack(track, stream);
    }
}

export function removeTrackFromPeers(track: MediaStreamTrack) {
    for (const connection of connectionsByParticipant.values()) {
        const sender = connection.getSenders().find((candidate) => candidate.track === track);
        if (sender) connection.removeTrack(sender);
    }
}

export async function replaceVideoTrackOnPeers(newTrack: MediaStreamTrack | null) {
    const transceivers = [...connectionsByParticipant.values()].flatMap(videoTransceivers);
    await Promise.all(transceivers.map((transceiver) => transceiver.sender.replaceTrack(newTrack)));
    await capUploads();
}

function onSignalingStep(type: SIGNAL_TYPE, step: (frame: SignalFrame) => Promise<void>) {
    on(type, (data) => {
        const frame = data as unknown as SignalFrame;
        applyInArrivalOrder(frame.from, () => step(frame));
    });
}

export function registerMeshHandlers() {
    on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, (data) => {
        for (const participantId of (data as ServerFrame & { participants: string[] }).participants) {
            void createPeerConnection(participantId, true);
        }
    });
    on(SIGNAL_TYPE.PARTICIPANT_LEFT, (data) => {
        removePeer(data.participant_id as string);
    });
    onSignalingStep(SIGNAL_TYPE.OFFER, answerOffer);
    onSignalingStep(SIGNAL_TYPE.ANSWER, applyAnswer);
    onSignalingStep(SIGNAL_TYPE.ICE_CANDIDATE, applyIceCandidate);
}
