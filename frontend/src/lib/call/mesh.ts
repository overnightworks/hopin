// The peer connections of the call and their signaling.

import { applyUploadCaps, videoTransceivers } from "#lib/call/bandwidth.ts";
import { files } from "#lib/call/files.ts";
import { LINE_CHANNEL_ID, LINE_CHANNEL_LABEL, lines } from "#lib/call/lines.ts";
import { reportConnectionOutcomes } from "#lib/call/outcome.ts";
import { monitorConnectionQuality } from "#lib/call/quality.ts";
import { watchIceConnection } from "#lib/call/recovery.ts";
import { on, send } from "#lib/call/signaling.ts";
import {
    type AnswerRelayFrame,
    ICE_SERVERS_PATH,
    type IceCandidateRelayFrame,
    type OfferRelayFrame,
    SIGNAL_TYPE,
} from "#lib/wire.ts";
import { type MonitoredConnection, monitorSpeaking } from "#lib/call/speaking.ts";
import { addRemoteTile, removeRemoteTile, showRemoteScreen, showRemoteStream } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";

let rtcConfiguration: RTCConfiguration = { iceServers: [] };

export class SignedOutError extends Error {}

export class IceServersUnavailableError extends Error {
    readonly status: number;

    constructor(status: number) {
        super(`ICE servers answered ${String(status)}`);
        this.status = status;
    }
}

export async function loadIceServers(): Promise<void> {
    const response = await fetch(ICE_SERVERS_PATH);
    if (response.status === 401) {
        throw new SignedOutError("Signed out");
    }
    if (!response.ok) {
        throw new IceServersUnavailableError(response.status);
    }
    const { iceServers } = (await response.json()) as { iceServers: RTCIceServer[] };
    rtcConfiguration = { iceServers };
}

interface SignalingQueue {
    tail: Promise<unknown>;
}

// Perfect negotiation: either side may offer once the first exchange is done. The person who joined offers first and
// is impolite; the one already there is polite and gives way when both offer at once.
interface Negotiation {
    readonly polite: boolean;
    makingOffer: boolean;
    ignoringOffer: boolean;
    // Tracks added before the first exchange ride in it; only a change after it needs a new offer.
    settled: boolean;
    // The first stream a person sends carries their camera and microphone, any later one their screen.
    cameraStreamId: string | null;
}

const negotiations = new WeakMap<RTCPeerConnection, Negotiation>();

function negotiationOf(connection: RTCPeerConnection): Negotiation {
    // createPeerConnection registers every connection it hands out.
    return negotiations.get(connection) as Negotiation;
}

// The transceivers that carry a screen, sent or received; the camera's changes never touch them.
const screenTransceivers = new WeakSet<RTCRtpTransceiver>();

function cameraTransceivers(connection: RTCPeerConnection) {
    return videoTransceivers(connection).filter((transceiver) => !screenTransceivers.has(transceiver));
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
    const negotiation = negotiationOf(connection);
    negotiation.makingOffer = true;
    try {
        const offer = await connection.createOffer(options);
        await connection.setLocalDescription(offer);
        send({
            type: SIGNAL_TYPE.OFFER,
            target: participantId,
            sdp: connection.localDescription,
        });
    } finally {
        negotiation.makingOffer = false;
    }
}

function addScreenTransceivers(connection: RTCPeerConnection, screenStream: MediaStream) {
    for (const track of screenStream.getTracks()) {
        // A transceiver of its own: addTrack would hand the track to an idle camera transceiver.
        screenTransceivers.add(connection.addTransceiver(track, { direction: "sendonly", streams: [screenStream] }));
    }
}

function capUploads() {
    return applyUploadCaps([...connectionsByParticipant.values()]);
}

async function createPeerConnection(participantId: string, initiator: boolean) {
    const connection = new RTCPeerConnection(rtcConfiguration);
    const negotiation: Negotiation = {
        polite: !initiator,
        makingOffer: false,
        ignoringOffer: false,
        settled: false,
        cameraStreamId: null,
    };
    negotiations.set(connection, negotiation);
    connectionsByParticipant.set(participantId, connection);
    // Negotiated, so both sides make it with the connection before the first offer is made or applied.
    lines.addPeer(
        participantId,
        connection.createDataChannel(LINE_CHANNEL_LABEL, { negotiated: true, id: LINE_CHANNEL_ID, ordered: true }),
    );
    // A file is fetched on a channel the fetching side opens in the connection, one per file.
    files.addPeer(participantId, { open: (label) => connection.createDataChannel(label, { ordered: true }) });
    connection.ondatachannel = (event) => {
        files.serve(participantId, event.channel);
    };

    if (state.localStream) {
        for (const track of state.localStream.getTracks()) {
            connection.addTrack(track, state.localStream);
        }
        if (initiator && state.localStream.getVideoTracks().length === 0) {
            connection.addTransceiver("video", { direction: "sendrecv", streams: [state.localStream] });
        }
    }
    if (state.screenStream) addScreenTransceivers(connection, state.screenStream);

    void capUploads();
    addRemoteTile(participantId);
    peersChanged();

    connection.ontrack = (event) => {
        const [stream] = event.streams;
        if (!stream) return;
        negotiation.cameraStreamId ??= stream.id;
        if (stream.id === negotiation.cameraStreamId) {
            showRemoteStream(participantId, stream);
            return;
        }
        screenTransceivers.add(event.transceiver);
        showRemoteScreen(participantId, stream);
        // The stream loses its tracks when the sender stops the share.
        stream.addEventListener("removetrack", () => {
            if (stream.getTracks().length === 0) showRemoteScreen(participantId, null);
        });
    };

    connection.onnegotiationneeded = () => {
        if (negotiation.settled) void sendOffer(participantId, connection).catch(reportError);
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
        negotiation.settled = true;
        watchIceConnection(connection, () => sendOffer(participantId, connection, { iceRestart: true }));
    }

    reportConnectionOutcomes(connection, (outcome) => {
        send({ type: SIGNAL_TYPE.CONNECTION_OUTCOME, outcome });
    });
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

// Only the first answer hands the camera over: a later offer's receive-only video is a screen being sent to us.
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

async function answerOffer(data: OfferRelayFrame) {
    const connection = connectionsByParticipant.get(data.from) ?? (await createPeerConnection(data.from, false));
    const negotiation = negotiationOf(connection);
    const collision = negotiation.makingOffer || connection.signalingState !== "stable";
    negotiation.ignoringOffer = !negotiation.polite && collision;
    if (negotiation.ignoringOffer) return;
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp as RTCSessionDescriptionInit));
    if (!negotiation.settled) await sendLocalVideoInAnswer(connection);
    if (isClosed(connection)) return;
    const answer = await connection.createAnswer();
    if (isClosed(connection)) return;
    // Set before the answer ends the exchange: the change that waited for it offers as soon as the state is stable.
    negotiation.settled = true;
    await connection.setLocalDescription(answer);
    send({
        type: SIGNAL_TYPE.ANSWER,
        target: data.from,
        sdp: connection.localDescription,
    });
    await capUploads();
}

async function applyAnswer(data: AnswerRelayFrame) {
    const connection = connectionsByParticipant.get(data.from);
    if (connection?.signalingState !== "have-local-offer") return;
    await connection.setRemoteDescription(new RTCSessionDescription(data.sdp as RTCSessionDescriptionInit));
}

async function applyIceCandidate(data: IceCandidateRelayFrame) {
    const connection = connectionsByParticipant.get(data.from);
    if (!connection || !data.candidate) return;
    try {
        await connection.addIceCandidate(new RTCIceCandidate(data.candidate));
    } catch (error) {
        // A candidate of an offer that was ignored has nothing to attach to.
        if (!negotiationOf(connection).ignoringOffer) throw error;
    }
}

function removePeer(participantId: string) {
    lines.removePeer(participantId);
    files.removePeer(participantId);
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

export function addScreenToPeers(screenStream: MediaStream) {
    for (const connection of connectionsByParticipant.values()) {
        addScreenTransceivers(connection, screenStream);
    }
}

export function removeScreenFromPeers(screenStream: MediaStream) {
    const tracks = screenStream.getTracks();
    for (const connection of connectionsByParticipant.values()) {
        for (const sender of connection.getSenders()) {
            if (sender.track && tracks.includes(sender.track)) connection.removeTrack(sender);
        }
    }
}

export async function replaceVideoTrackOnPeers(newTrack: MediaStreamTrack | null) {
    const transceivers = [...connectionsByParticipant.values()].flatMap(cameraTransceivers);
    await Promise.all(transceivers.map((transceiver) => transceiver.sender.replaceTrack(newTrack)));
    await capUploads();
}

export function registerMeshHandlers() {
    on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, (data) => {
        for (const participantId of data.participants) {
            void createPeerConnection(participantId, true);
        }
    });
    on(SIGNAL_TYPE.PARTICIPANT_LEFT, (data) => {
        removePeer(data.participant_id);
    });
    on(SIGNAL_TYPE.OFFER, (frame) => {
        applyInArrivalOrder(frame.from, () => answerOffer(frame));
    });
    on(SIGNAL_TYPE.ANSWER, (frame) => {
        applyInArrivalOrder(frame.from, () => applyAnswer(frame));
    });
    on(SIGNAL_TYPE.ICE_CANDIDATE, (frame) => {
        applyInArrivalOrder(frame.from, () => applyIceCandidate(frame));
    });
}
