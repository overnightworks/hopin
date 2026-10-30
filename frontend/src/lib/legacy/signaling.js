import { ERROR_REASON, SESSION_ENDED_CLOSE_CODE, SIGNAL_TYPE, WEBSOCKET_PATH } from "#lib/wire.ts";
import { SentLog } from "./replay.js";
import { getRoomIdFromUrl } from "./router.js";
import { state } from "#lib/call/state.ts";
import { setStatus } from "#lib/ui/status.svelte.ts";

export { SIGNAL_TYPE, ERROR_REASON };
export { ROOM_CLOSED_REASON } from "#lib/wire.ts";

const NORMAL_CLOSURE_CODE = 1000;
export const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 8000, 6000];

const handlersByType = new Map();
let socket = null;
let socketIsLive = false;
let reconnectTimer = null;
let seat = null;
let sentLog = new SentLog();
let lastDispatchedSeq = 0;
let sessionLost = () => {};
let sessionEnded = () => {};

export function on(type, handler) {
    handlersByType.set(type, [...(handlersByType.get(type) ?? []), handler]);
}

export function onSessionLost(callback) {
    sessionLost = callback;
}

export function onSessionEnded(callback) {
    sessionEnded = callback;
}

export function send(message) {
    if (socket === null) {
        throw new Error("No signaling session: connect() before send()");
    }
    const frame = sentLog.append(message);
    if (socketIsLive) socket.send(JSON.stringify(frame));
}

export function isConnected() {
    return socket !== null;
}

function dispatch(data) {
    for (const handler of handlersByType.get(data.type) ?? []) {
        handler(data);
    }
}

function joinMessage() {
    const message = { type: SIGNAL_TYPE.JOIN };
    const urlRoomId = getRoomIdFromUrl();
    if (urlRoomId) {
        message.room_id = urlRoomId;
    } else if (state.roomId) {
        message.room_id = state.roomId;
    }
    return message;
}

function resumeMessage() {
    return {
        type: SIGNAL_TYPE.RESUME,
        participant_id: seat.participantId,
        token: seat.token,
        last_seq: lastDispatchedSeq,
        sent_floor: sentLog.floor,
    };
}

function writeFirstFrame(message) {
    socket.send(JSON.stringify(message));
}

function startWriting() {
    socketIsLive = true;
    for (const frame of sentLog.unacknowledged()) {
        socket.send(JSON.stringify(frame));
    }
}

function forgetSeat() {
    seat = null;
    sentLog = new SentLog();
    lastDispatchedSeq = 0;
}

function handleOpen() {
    setStatus("Connected - joining...", "connecting");
    if (seat) {
        writeFirstFrame(resumeMessage());
    } else {
        writeFirstFrame(joinMessage());
        startWriting();
    }
}

function handleResumed(data) {
    seat.token = data.resume_token;
    sentLog.acknowledge(data.last_seq);
    startWriting();
    state.reconnectAttempt = 0;
    setStatus("In call", "connected");
}

function fallBackToJoin() {
    forgetSeat();
    sessionLost();
    startWriting();
    send(joinMessage());
}

function keepSeat(data) {
    seat = { participantId: data.participant_id, token: data.resume_token };
}

function isAlreadyDispatched(data) {
    if (data.seq === undefined) return false;
    if (data.seq <= lastDispatchedSeq) return true;
    lastDispatchedSeq = data.seq;
    return false;
}

function handleMessage(event) {
    const data = JSON.parse(event.data);
    if (data.type === SIGNAL_TYPE.RESUMED) {
        handleResumed(data);
    } else if (data.type === SIGNAL_TYPE.ERROR && data.reason === ERROR_REASON.RESUME_REFUSED) {
        fallBackToJoin();
    } else if (!isAlreadyDispatched(data)) {
        if (data.resume_token) keepSeat(data);
        dispatch(data);
    }
}

function endSession() {
    socket = null;
    socketIsLive = false;
    forgetSeat();
}

function scheduleReconnect() {
    const delay = RECONNECT_DELAYS_MS[state.reconnectAttempt];
    state.reconnectAttempt++;
    setStatus(`Reconnecting in ${delay / 1000}s...`, "connecting");
    reconnectTimer = setTimeout(connect, delay);
}

function handleClose(event) {
    socketIsLive = false;
    if (state.intentionalLeave) return;
    if (event.code === SESSION_ENDED_CLOSE_CODE) {
        endSession();
        sessionEnded();
        return;
    }
    const heldSeat = seat !== null;
    if (heldSeat && state.reconnectAttempt < RECONNECT_DELAYS_MS.length) {
        scheduleReconnect();
        return;
    }
    endSession();
    sessionLost();
    if (heldSeat) {
        setStatus("Connection lost", "error");
    } else {
        setStatus("Disconnected", "disconnected");
    }
}

export function connect() {
    const scheme = location.protocol.replace("http", "ws");
    const current = new WebSocket(`${scheme}//${location.host}${WEBSOCKET_PATH}`);
    socket = current;

    const ifCurrent = (handler) => (event) => {
        if (current === socket) handler(event);
    };
    current.onopen = ifCurrent(handleOpen);
    current.onmessage = ifCurrent(handleMessage);
    current.onclose = ifCurrent(handleClose);
    current.onerror = ifCurrent(() => {
        setStatus("Connection error", "error");
    });
}

export function disconnect() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
    socket?.close(NORMAL_CLOSURE_CODE);
    endSession();
}
